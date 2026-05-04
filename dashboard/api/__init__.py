"""Dashboard HTTP API — router aggregation.

Stage 8 added ``/api/health``. Stage 9 adds the read endpoints for
projects, jobs, stages, HIL, artifacts, costs, and the observatory stub.
Stage 10 adds SSE endpoints under ``/sse/``.
Stage 15 adds stage action endpoints (chat, cancel, restart).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from dashboard.api.artifacts import router as artifacts_router
from dashboard.api.chat import router as chat_router
from dashboard.api.costs import router as costs_router
from dashboard.api.hil import router as hil_router
from dashboard.api.jobs import router as jobs_router
from dashboard.api.observatory import router as observatory_router
from dashboard.api.projects import router as projects_router
from dashboard.api.settings import router as settings_router
from dashboard.api.sse import router as sse_router
from dashboard.api.stages import router as stages_router


class HealthResponse(BaseModel):
    """Response shape for ``GET /api/health``."""

    ok: bool
    cache_size: int
    # Stage 16 follow-up: surface the active runner mode + claude binary
    # so operators can confirm at a glance whether this dashboard will
    # spawn jobs against real Claude (and incur real spend) or against
    # FakeStageRunner. Mirrors the startup log line in dashboard/__main__.
    runner_mode: str
    claude_binary: str | None


router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Return server liveness, cache size, and active runner-mode info."""
    cache = request.app.state.cache  # type: ignore[attr-defined]
    settings = request.app.state.settings  # type: ignore[attr-defined]
    return HealthResponse(
        ok=True,
        cache_size=sum(cache.size().values()),
        runner_mode=settings.runner_mode,
        claude_binary=settings.claude_binary if settings.runner_mode == "real" else None,
    )


# Mount the per-resource routers under the same top-level router so
# ``app.include_router(router)`` in ``create_app`` picks them all up.
router.include_router(projects_router)
router.include_router(jobs_router)
router.include_router(stages_router)
router.include_router(chat_router)
router.include_router(hil_router)
router.include_router(artifacts_router)
router.include_router(costs_router)
router.include_router(observatory_router)
router.include_router(settings_router)
router.include_router(sse_router)
