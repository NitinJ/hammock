"""Observatory metrics endpoint.

Per design doc § URL topology — ``GET /api/observatory/metrics`` is a v0
stub that returns an empty payload. Soul / Council surfaces (which feed
this endpoint) land in v2+; the route exists now so the frontend can
reference it without conditional logic.
"""

from __future__ import annotations

from fastapi import APIRouter

from dashboard.state.projections import ObservatoryMetrics

router = APIRouter(prefix="/api/observatory", tags=["observatory"])


@router.get("/metrics", response_model=ObservatoryMetrics)
async def metrics() -> ObservatoryMetrics:
    return ObservatoryMetrics()
