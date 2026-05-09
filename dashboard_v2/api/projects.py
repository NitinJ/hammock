"""Projects management endpoints — register / list / detail / verify / delete.

A project is a registered local git checkout. Workflows submit against
a project; the runner clones ``repo_path`` into ``<job_dir>/repo`` per
job. We never touch the operator's working tree.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dashboard_v2 import projects as proj
from dashboard_v2.settings import load_settings

log = logging.getLogger(__name__)

router = APIRouter()


# -------------------- Models --------------------


class ProjectRegisterRequest(BaseModel):
    repo_path: str = Field(..., min_length=1)
    slug: str | None = None
    name: str | None = None


class ProjectResponse(BaseModel):
    slug: str
    name: str
    repo_path: str
    registered_at: str
    default_branch: str | None
    health: dict[str, Any]


# -------------------- Endpoints --------------------


@router.post("/projects", status_code=201, response_model=ProjectResponse)
def register_project(body: ProjectRegisterRequest) -> ProjectResponse:
    settings = load_settings()
    repo_path = Path(body.repo_path).expanduser().resolve()
    if body.slug:
        try:
            slug = proj.normalize_slug(body.slug)
        except proj.ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        try:
            slug = proj.derive_slug_from_path(repo_path)
        except proj.ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    existing = proj.read_project(slug, settings.root)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"project {slug!r} already registered (path={existing['repo_path']})",
        )
    health = proj.health_check(repo_path)
    if not health["path_exists"]:
        raise HTTPException(
            status_code=400,
            detail=f"path {repo_path} does not exist or is not a directory",
        )
    if not health["is_git_repo"]:
        raise HTTPException(
            status_code=400,
            detail=f"path {repo_path} is not a git repo (no .git/ found)",
        )
    proj.write_project(
        slug=slug,
        repo_path=repo_path,
        name=body.name,
        root=settings.root,
        default_branch=health["default_branch"],
    )
    data = proj.read_project(slug, settings.root)
    if data is None:  # pragma: no cover — race
        raise HTTPException(status_code=500, detail="failed to read back registered project")
    data["health"] = health
    return ProjectResponse(**data)


@router.get("/projects")
def list_all_projects() -> dict[str, Any]:
    settings = load_settings()
    return {"projects": proj.list_projects(settings.root)}


@router.get("/projects/{slug}", response_model=ProjectResponse)
def get_project(slug: str) -> ProjectResponse:
    settings = load_settings()
    try:
        slug = proj.normalize_slug(slug)
    except proj.ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = proj.read_project(slug, settings.root)
    if data is None:
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")
    repo_path = Path(data.get("repo_path", ""))
    data["health"] = proj.health_check(repo_path)
    return ProjectResponse(**data)


@router.post("/projects/{slug}/verify", response_model=ProjectResponse)
def verify_project(slug: str) -> ProjectResponse:
    """Re-run health check; refresh default_branch on disk if changed."""
    settings = load_settings()
    try:
        slug = proj.normalize_slug(slug)
    except proj.ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = proj.read_project(slug, settings.root)
    if data is None:
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")
    repo_path = Path(data["repo_path"])
    health = proj.health_check(repo_path)
    new_branch = health["default_branch"]
    if new_branch and new_branch != data.get("default_branch"):
        proj.write_project(
            slug=slug,
            repo_path=repo_path,
            name=data.get("name"),
            root=settings.root,
            registered_at=data.get("registered_at"),
            default_branch=new_branch,
        )
        data["default_branch"] = new_branch
    data["health"] = health
    return ProjectResponse(**data)


@router.delete("/projects/{slug}")
def delete_project_endpoint(slug: str) -> dict[str, Any]:
    settings = load_settings()
    try:
        slug = proj.normalize_slug(slug)
    except proj.ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not proj.delete_project(slug, settings.root):
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")
    return {"slug": slug, "deleted": True}


__all__ = ["router"]
