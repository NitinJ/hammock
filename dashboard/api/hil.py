"""HIL plane endpoints — read + write.

Per design doc § Presentation plane § URL topology and § HIL bridge.
Stage 9 ships the read endpoints; Stage 13 adds:
  - ``GET  /api/hil/{id}``        → enriched envelope (HilItemDetail)
  - ``GET  /api/hil/templates/{name}`` → resolved UiTemplate
  - ``POST /api/hil/{id}/answer`` → submit answer via HilContract
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from dashboard.hil.contract import ConflictError, HilContract, NotFoundError
from dashboard.hil.template_registry import TemplateKindConflictError, TemplateNotFoundError, TemplateRegistry
from dashboard.state import projections
from dashboard.state.projections import HilQueueItem
from shared.models import HilItem
from shared.models.hil import HilAnswer
from shared.models.presentation import UiTemplate

router = APIRouter(prefix="/api/hil", tags=["hil"])

HilStatus = Literal["awaiting", "answered", "cancelled"]
HilKind = Literal["ask", "review", "manual-step"]

# Kind → default template name (used when stage definition has no ui_template)
_KIND_DEFAULT_TEMPLATE: dict[str, str] = {
    "ask": "ask-default-form",
    "review": "spec-review-form",
    "manual-step": "manual-step-default-form",
}


class HilItemDetail(BaseModel):
    """Enriched HIL item envelope returned by GET /api/hil/{id}."""

    item: HilItem
    job_slug: str | None
    project_slug: str | None
    ui_template_name: str


@router.get("", response_model=list[HilQueueItem])
async def list_hil(
    request: Request,
    status: Annotated[HilStatus | None, Query()] = "awaiting",
    kind: Annotated[HilKind | None, Query()] = None,
    project: Annotated[str | None, Query(description="filter by project slug")] = None,
    job: Annotated[str | None, Query(description="filter by job slug")] = None,
) -> list[HilQueueItem]:
    cache = request.app.state.cache  # type: ignore[attr-defined]
    return projections.hil_queue(
        cache,
        status=status,
        kind=kind,
        project_slug=project,
        job_slug=job,
    )


@router.get("/templates/{name}", response_model=UiTemplate)
async def get_template(
    request: Request,
    name: str,
    project_slug: Annotated[str | None, Query(description="project slug for per-project override")] = None,
) -> UiTemplate:
    """Return the resolved UI template for *name*.

    Resolves per-project-first if *project_slug* is given and the project has
    a ``.hammock/ui-templates/<name>.json`` override.
    """
    raise NotImplementedError


@router.get("/{item_id}", response_model=HilItemDetail)
async def get_hil(request: Request, item_id: str) -> HilItemDetail:
    """Return the HIL item enriched with job/project context and template name."""
    raise NotImplementedError


@router.post("/{item_id}/answer", response_model=HilItem)
async def submit_answer(request: Request, item_id: str, answer: HilAnswer) -> HilItem:
    """Submit an answer for a HIL item.

    Calls ``HilContract.submit_answer``.  Idempotent for identical re-submits;
    returns 409 on a conflicting re-submit; 404 when item not found.
    """
    raise NotImplementedError
