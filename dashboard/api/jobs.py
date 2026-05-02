"""Read endpoints for jobs.

Per design doc § Presentation plane § URL topology. Stage 9 ships only
``GET`` here — submit (``POST /api/jobs``) lands in Stage 14, cancel /
restart / chat (POST sub-resources) land alongside the live stage view in
Stages 14 and 15.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from dashboard.state import projections
from dashboard.state.projections import JobDetail, JobListItem
from shared.models import JobState

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


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
