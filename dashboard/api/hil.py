"""Read endpoints for the HIL plane.

Per design doc § Presentation plane § URL topology and § HIL bridge.
Stage 9 ships the read endpoints; ``POST /api/hil/{id}/answer`` lands in
Stage 13 alongside the form pipeline.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from dashboard.state import projections
from dashboard.state.projections import HilQueueItem
from shared.models import HilItem

router = APIRouter(prefix="/api/hil", tags=["hil"])

HilStatus = Literal["awaiting", "answered", "cancelled"]
HilKind = Literal["ask", "review", "manual-step"]


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


@router.get("/{item_id}", response_model=HilItem)
async def get_hil(request: Request, item_id: str) -> HilItem:
    cache = request.app.state.cache  # type: ignore[attr-defined]
    item = cache.get_hil(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"hil item {item_id!r} not found")
    return item
