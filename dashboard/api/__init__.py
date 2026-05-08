"""Dashboard HTTP API — router aggregation.

v1 surface:
  - GET  /api/health
  - GET  /api/projects                                   (projection)
  - GET  /api/projects/{slug}                            (projection)
  - GET  /api/workflows                                  (bundled YAMLs)
  - GET  /api/jobs                                       (jobs.py)
  - GET  /api/jobs/{slug}                                (jobs.py)
  - GET  /api/jobs/{slug}/nodes/{node_id}                (jobs.py)
  - GET  /api/jobs/{slug}/nodes/{node_id}/chat           (jobs.py)
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

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from dashboard.api.hil import router as hil_router
from dashboard.api.jobs import router as jobs_router
from dashboard.api.projects import router as projects_router
from dashboard.api.settings import router as settings_router
from dashboard.api.sse import router as sse_router
from engine.v1.loader import WorkflowLoadError, load_workflow

log = logging.getLogger(__name__)


class HealthResponse(BaseModel):
    """Response shape for ``GET /api/health``."""

    ok: bool
    runner_mode: str
    claude_binary: str | None


class WorkflowListItem(BaseModel):
    """One entry in ``GET /api/workflows``.

    ``job_type`` is the workflow folder name; the dashboard submits
    ``POST /api/jobs`` with this string and the compiler resolves it
    back to ``hammock/templates/workflows/<job_type>/workflow.yaml``."""

    job_type: str
    workflow_name: str


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


_BUNDLED_WORKFLOWS_DIR = Path(__file__).parent.parent.parent / "hammock" / "templates" / "workflows"


@router.get("/api/workflows", response_model=list[WorkflowListItem])
async def list_workflows() -> list[WorkflowListItem]:
    """List bundled workflows available for ``POST /api/jobs``.

    Each bundled workflow lives under
    ``hammock/templates/workflows/<job_type>/workflow.yaml`` with a
    sibling ``prompts/`` directory. ``job_type`` is the folder name;
    the loader's ``workflow:`` field provides ``workflow_name``.
    Malformed YAMLs are logged and omitted so one bad file doesn't
    blank the dropdown."""
    if not _BUNDLED_WORKFLOWS_DIR.is_dir():
        return []
    out: list[WorkflowListItem] = []
    for folder in sorted(p for p in _BUNDLED_WORKFLOWS_DIR.iterdir() if p.is_dir()):
        wf_path = folder / "workflow.yaml"
        if not wf_path.is_file():
            continue
        try:
            wf = load_workflow(wf_path)
        except WorkflowLoadError as exc:
            log.warning("skipping unloadable workflow %s: %s", wf_path, exc)
            continue
        out.append(WorkflowListItem(job_type=folder.name, workflow_name=wf.workflow))
    return out


router.include_router(jobs_router)
router.include_router(hil_router)
router.include_router(projects_router)
router.include_router(settings_router)
router.include_router(sse_router)
