"""Dashboard HTTP API — router aggregation.

v1 surface (Stage 3 cutover):
  - GET  /api/health
  - GET  /api/jobs                                       (jobs.py)
  - GET  /api/jobs/{slug}                                (jobs.py)
  - POST /api/jobs                                       (jobs.py)
  - GET  /api/hil                                        (hil.py)
  - GET  /api/hil/{slug}                                 (hil.py)
  - GET  /api/hil/{slug}/{node_id}                       (hil.py)
  - POST /api/hil/{slug}/{node_id}/answer                (hil.py)
  - GET  /sse/global                                     (sse.py)
  - GET  /sse/job/{slug}                                 (sse.py)
  - GET  /sse/node/{slug}/{node_id}                      (sse.py)
  - GET  /api/settings                                   (settings.py)

v0-only handlers retired in Stage 3 (rebuilt around the v1 node
primitive in Stage 6): projects, stages, chat, artifacts, costs,
observatory, stage_actions. The frontend rebuild adds whatever
endpoints it needs.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from dashboard.api.hil import router as hil_router
from dashboard.api.jobs import router as jobs_router
from dashboard.api.settings import router as settings_router
from dashboard.api.sse import router as sse_router


class HealthResponse(BaseModel):
    """Response shape for ``GET /api/health``."""

    ok: bool
    runner_mode: str
    claude_binary: str | None


router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Return server liveness + active runner-mode info."""
    settings = request.app.state.settings  # type: ignore[attr-defined]
    return HealthResponse(
        ok=True,
        runner_mode=settings.runner_mode,
        claude_binary=settings.claude_binary if settings.runner_mode == "real" else None,
    )


router.include_router(jobs_router)
router.include_router(hil_router)
router.include_router(settings_router)
router.include_router(sse_router)
