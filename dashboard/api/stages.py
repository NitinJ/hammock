"""Stage read endpoints and Stage-15 action endpoints.

Per design doc § Presentation plane § URL topology.
- Stage 9: GET /api/jobs/{job_slug}/stages/{stage_id} (read)
- Stage 15: POST /cancel, POST /restart (state-changing actions)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from dashboard.driver import ipc, lifecycle
from dashboard.state import projections
from dashboard.state.cache import Cache
from dashboard.state.projections import (
    ActiveStageStripItem,
    StageDetail,
)

MAX_STAGE_RESTARTS = 3

router = APIRouter(tags=["stages"])


class CancelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool = True


class RestartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_driver_pid: int


def _assert_stage_exists(cache: Cache, job_slug: str, stage_id: str) -> None:
    if cache.get_job(job_slug) is None:
        raise HTTPException(status_code=404, detail=f"job {job_slug!r} not found")
    if cache.get_stage(job_slug, stage_id) is None:
        raise HTTPException(
            status_code=404, detail=f"stage {stage_id!r} not found in job {job_slug!r}"
        )


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


@router.post(
    "/api/jobs/{job_slug}/stages/{stage_id}/cancel",
    response_model=CancelResponse,
)
async def cancel_stage(request: Request, job_slug: str, stage_id: str) -> CancelResponse:
    """Write the cancel command file; the Job Driver picks it up on its next poll."""
    cache: Cache = request.app.state.cache  # type: ignore[attr-defined]
    root = request.app.state.settings.root  # type: ignore[attr-defined]
    _assert_stage_exists(cache, job_slug, stage_id)
    ipc.write_cancel_command(job_slug, root=root, reason="human")
    return CancelResponse()


@router.post(
    "/api/jobs/{job_slug}/stages/{stage_id}/restart",
    response_model=RestartResponse,
)
async def restart_stage(request: Request, job_slug: str, stage_id: str) -> RestartResponse:
    """Re-spawn the Job Driver for the given stage.

    Returns 409 when ``restart_count >= MAX_STAGE_RESTARTS`` to prevent
    unbounded restarts.
    """
    cache: Cache = request.app.state.cache  # type: ignore[attr-defined]
    root = request.app.state.settings.root  # type: ignore[attr-defined]
    _assert_stage_exists(cache, job_slug, stage_id)

    stage = cache.get_stage(job_slug, stage_id)
    assert stage is not None  # asserted above
    if stage.restart_count >= MAX_STAGE_RESTARTS:
        raise HTTPException(
            status_code=409,
            detail=(
                f"stage {stage_id!r} has reached the maximum restart limit "
                f"({MAX_STAGE_RESTARTS})"
            ),
        )

    pid = await lifecycle.spawn_driver(job_slug, root=root)
    return RestartResponse(job_driver_pid=pid)
