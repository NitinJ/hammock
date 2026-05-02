"""Read endpoint for stage detail.

Per design doc § Presentation plane § URL topology. The URL topology lists
state-changing POSTs under ``/api/jobs/{job_slug}/stages/{stage_id}/...``;
Stage 9 ships the read counterpart so the live stage view (Stage 15) has
data to render.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from dashboard.state import projections
from dashboard.state.projections import (
    ActiveStageStripItem,
    StageDetail,
)

router = APIRouter(tags=["stages"])


@router.get(
    "/api/jobs/{job_slug}/stages/{stage_id}",
    response_model=StageDetail,
)
async def get_stage(request: Request, job_slug: str, stage_id: str) -> StageDetail:
    cache = request.app.state.cache  # type: ignore[attr-defined]
    detail = projections.stage_detail(cache, job_slug, stage_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail=f"stage {stage_id!r} not found in job {job_slug!r}",
        )
    return detail


@router.get(
    "/api/active-stages",
    response_model=list[ActiveStageStripItem],
)
async def active_stages(request: Request) -> list[ActiveStageStripItem]:
    """Active stages strip data for the dashboard home.

    Per design doc § View inventory § Dashboard home. Returns every stage
    in state ``RUNNING`` or ``ATTENTION_NEEDED`` across all jobs.
    """
    cache = request.app.state.cache  # type: ignore[attr-defined]
    return projections.active_stage_strip(cache)
