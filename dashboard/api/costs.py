"""Cost rollup endpoint.

Per design doc § Accounting Ledger and § URL topology. ``cost_accrued``
events live in append-only ``events.jsonl`` files; the rollup folds them
on demand.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request

from dashboard.state import projections
from dashboard.state.projections import CostRollup

router = APIRouter(prefix="/api/costs", tags=["costs"])

CostScope = Literal["project", "job", "stage"]


@router.get("", response_model=CostRollup)
async def get_cost_rollup(
    request: Request,
    scope: Annotated[CostScope, Query(description="rollup scope")],
    id: Annotated[str, Query(description="project slug, job slug, or stage id")],
    job: Annotated[
        str | None,
        Query(description="required when scope=stage — the job slug owning the stage"),
    ] = None,
) -> CostRollup:
    cache = request.app.state.cache  # type: ignore[attr-defined]
    if scope == "stage" and job is None:
        raise HTTPException(
            status_code=422,
            detail="?job=<slug> is required when scope=stage",
        )
    rollup = projections.cost_rollup(cache, scope, id, stage_job_slug=job)
    if rollup is None:
        raise HTTPException(status_code=404, detail=f"{scope} {id!r} not found")
    return rollup
