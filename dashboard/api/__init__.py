"""Dashboard HTTP API — router aggregation.

v1 surface:
  - GET  /api/health
  - GET  /api/projects                                   (projection)
  - GET  /api/projects/{slug}                            (projection)
  - GET  /api/jobs                                       (jobs.py)
  - GET  /api/jobs/{slug}                                (jobs.py)
  - GET  /api/jobs/{slug}/nodes/{node_id}                (jobs.py)
  - POST /api/jobs                                       (jobs.py)
  - GET  /api/hil                                        (hil.py)
  - GET  /api/hil/{slug}                                 (hil.py)
  - GET  /api/hil/{slug}/{node_id}                       (hil.py)
  - POST /api/hil/{slug}/{node_id}/answer                (hil.py)
  - GET  /api/hil/{slug}/asks/{call_id}                  (hil.py)
  - POST /api/hil/{slug}/asks/{call_id}/answer           (hil.py)
  - GET  /api/settings                                   (settings.py)
  - GET  /sse/global                                     (sse.py)
  - GET  /sse/job/{slug}                                 (sse.py)
  - GET  /sse/node/{slug}/{node_id}                      (sse.py)

The v0 routes (`/api/stages`, `/api/costs`, `/api/observatory`,
`/api/chat`, `/sse/stage`) retired in Stage 3; per-node detail replaces
them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from dashboard.api.hil import router as hil_router
from dashboard.api.jobs import router as jobs_router
from dashboard.api.settings import router as settings_router
from dashboard.api.sse import router as sse_router
from dashboard.state import projections
from dashboard.state.projections import ProjectDetail, ProjectListItem


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


@router.get("/api/projects", response_model=list[ProjectListItem])
async def list_projects(request: Request) -> list[ProjectListItem]:
    """Enumerate registered projects on disk under ``<root>/projects/``."""
    settings = request.app.state.settings  # type: ignore[attr-defined]
    return projections.project_list(settings.root)


@router.get("/api/projects/{slug}", response_model=ProjectDetail)
async def get_project(request: Request, slug: str) -> ProjectDetail:
    settings = request.app.state.settings  # type: ignore[attr-defined]
    detail = projections.project_detail(settings.root, slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")
    return detail


router.include_router(jobs_router)
router.include_router(hil_router)
router.include_router(settings_router)
router.include_router(sse_router)
