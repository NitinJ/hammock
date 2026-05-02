"""Dashboard HTTP API — router aggregation.

Stage 8 ships only the ``/api/health`` endpoint.  Subsequent stages add
routers here (projects, jobs, stages, HIL, SSE, …).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response shape for ``GET /api/health``."""

    ok: bool
    cache_size: int


router = APIRouter()


@router.get("/api/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Return server liveness and aggregate cache entry count."""
    raise NotImplementedError
