"""Dashboard HTTP API — router aggregation.

Stage 8 added ``/api/health``. Stage 9 adds the read endpoints for
projects, jobs, stages, HIL, artifacts, costs, and the observatory stub.

Subsequent stages (10 SSE, 13 HIL POST, 14 jobs POST, 15 stage POST) add
their own routers here.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from dashboard.api.artifacts import router as artifacts_router
from dashboard.api.costs import router as costs_router
from dashboard.api.hil import router as hil_router
from dashboard.api.jobs import router as jobs_router
from dashboard.api.observatory import router as observatory_router
from dashboard.api.projects import router as projects_router
from dashboard.api.stages import router as stages_router


class HealthResponse(BaseModel):
    """Response shape for ``GET /api/health``."""

    ok: bool
    cache_size: int


router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Return server liveness and aggregate cache entry count."""
    cache = request.app.state.cache  # type: ignore[attr-defined]
    return HealthResponse(ok=True, cache_size=sum(cache.size().values()))


# Mount the per-resource routers under the same top-level router so
# ``app.include_router(router)`` in ``create_app`` picks them all up.
router.include_router(projects_router)
router.include_router(jobs_router)
router.include_router(stages_router)
router.include_router(hil_router)
router.include_router(artifacts_router)
router.include_router(costs_router)
router.include_router(observatory_router)
