"""Read endpoints for the project registry.

Per design doc § Presentation plane § URL topology and § Cache → view
projections. Routes here are read-only; ``POST /api/projects`` (register)
and ``PATCH /api/projects/{slug}`` (rename) land in later stages.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from dashboard.state import projections
from dashboard.state.projections import ProjectDetail, ProjectListItem

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("", response_model=list[ProjectListItem])
async def list_projects(request: Request) -> list[ProjectListItem]:
    cache = request.app.state.cache  # type: ignore[attr-defined]
    return projections.project_list(cache)


@router.get("/{slug}", response_model=ProjectDetail)
async def get_project(request: Request, slug: str) -> ProjectDetail:
    cache = request.app.state.cache  # type: ignore[attr-defined]
    detail = projections.project_detail(cache, slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")
    return detail
