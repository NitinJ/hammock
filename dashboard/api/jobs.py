"""Job endpoints: list, detail, and submit.

Per design doc § Presentation plane § URL topology. Stage 9 ships ``GET``
endpoints; Stage 14 adds ``POST /api/jobs`` (compile + spawn driver).
Cancel / restart / chat POST sub-resources land in Stage 15.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dashboard.code.branches import create_job_branch
from dashboard.compiler.compile import compile_job
from dashboard.driver.lifecycle import spawn_driver
from dashboard.state import projections
from dashboard.state.projections import JobDetail, JobListItem
from shared import paths
from shared.models import JobState, ProjectConfig

log = logging.getLogger(__name__)

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
        # v0 alignment Plan #2 + #8: create the per-job branch in the
        # project's repo before spawning the driver. Best-effort: if the
        # repo isn't a real git repo (some test fixtures point at fake
        # paths), log a warning and continue. Real registrations always
        # have a real repo.
        _create_job_branch_best_effort(
            project_slug=body.project_slug,
            job_slug=result.job_slug,
            root=settings.root,
        )
        await spawn_driver(
            result.job_slug,
            root=settings.root,
            fake_fixtures_dir=settings.fake_fixtures_dir,
            claude_binary=settings.claude_binary,
        )
        return JobSubmitResponse(job_slug=result.job_slug, dry_run=False)

    stages_out = [s.model_dump(mode="json") for s in result.stages]
    return JobSubmitResponse(job_slug=result.job_slug, dry_run=True, stages=stages_out)


def _create_job_branch_best_effort(
    *,
    project_slug: str,
    job_slug: str,
    root: Path | None,
) -> None:
    """Read project.json + create ``hammock/jobs/<slug>`` in the repo.

    Failure modes (logged, never raised):

    - project.json missing or unreadable
    - repo_path doesn't exist on disk
    - repo_path isn't a git repo (fake-fixture test scenarios)
    - git command failure for any other reason

    A real registration verifies the repo at register time, so in
    production this should always succeed. The best-effort posture is
    purely to keep the existing test suite (which uses synthetic
    repo paths) and the v0 fake-fixture flows working.
    """
    try:
        project = ProjectConfig.model_validate_json(
            paths.project_json(project_slug, root=root).read_text()
        )
    except (FileNotFoundError, ValueError) as exc:
        log.warning(
            "could not read project.json for %s: %s — skipping job-branch creation",
            project_slug,
            exc,
        )
        return

    repo = Path(project.repo_path)
    if not (repo / ".git").exists():
        log.warning(
            "%s is not a git repo — skipping job-branch creation for %s",
            repo,
            job_slug,
        )
        return

    try:
        create_job_branch(repo, job_slug, base=project.default_branch)
    except subprocess.CalledProcessError as exc:
        log.warning(
            "failed to create hammock/jobs/%s in %s: %s — stages will lack isolation",
            job_slug,
            repo,
            exc,
        )
