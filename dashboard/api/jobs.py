"""Job endpoints: list, detail, and submit.

Per design doc § Presentation plane § URL topology. Stage 9 ships ``GET``
endpoints; Stage 14 adds ``POST /api/jobs`` (compile + spawn driver).
Cancel / restart / chat POST sub-resources land in Stage 15.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dashboard.compiler.compile import compile_job
from dashboard.driver.lifecycle import spawn_driver
from dashboard.state import projections
from dashboard.state.projections import JobDetail, JobListItem
from shared.models import JobState

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Submit (POST /api/jobs) — request / response shapes
# ---------------------------------------------------------------------------


class JobSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_slug: str = Field(min_length=1)
    job_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    request_text: str = Field(min_length=1)
    dry_run: bool = False


class CompileFailureOut(BaseModel):
    kind: str
    stage_id: str | None
    message: str


class JobSubmitResponse(BaseModel):
    job_slug: str
    dry_run: bool
    stages: list[dict[str, Any]] | None = None


@router.get("", response_model=list[JobListItem])
async def list_jobs(
    request: Request,
    project: Annotated[str | None, Query(description="filter by project slug")] = None,
    status: Annotated[JobState | None, Query(description="filter by job state")] = None,
) -> list[JobListItem]:
    cache = request.app.state.cache  # type: ignore[attr-defined]
    return projections.job_list(cache, project_slug=project, status=status)


@router.get("/{job_slug}", response_model=JobDetail)
async def get_job(request: Request, job_slug: str) -> JobDetail:
    cache = request.app.state.cache  # type: ignore[attr-defined]
    detail = projections.job_detail(cache, job_slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"job {job_slug!r} not found")
    return detail


@router.post("", response_model=JobSubmitResponse, status_code=201)
async def submit_job(body: JobSubmitRequest, request: Request) -> JobSubmitResponse:
    settings = request.app.state.settings  # type: ignore[attr-defined]

    result = compile_job(
        project_slug=body.project_slug,
        job_type=body.job_type,
        title=body.title,
        request_text=body.request_text,
        root=settings.root,
        dry_run=body.dry_run,
    )

    if isinstance(result, list):
        raise HTTPException(
            status_code=422,
            detail=[{"kind": f.kind, "stage_id": f.stage_id, "message": f.message} for f in result],
        )

    if not result.dry_run:
        await spawn_driver(
            result.job_slug,
            root=settings.root,
            fake_fixtures_dir=settings.fake_fixtures_dir,
        )
        return JobSubmitResponse(job_slug=result.job_slug, dry_run=False)

    stages_out = [s.model_dump(mode="json") for s in result.stages]
    return JobSubmitResponse(job_slug=result.job_slug, dry_run=True, stages=stages_out)
