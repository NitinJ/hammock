"""Aggregate prompts API.

Returns a flat list of prompts across the bundled `hammock/prompts/`
directory and every registered project's `<repo>/.hammock-v2/prompts/`.
Per-project CRUD lives under ``/api/projects/{slug}/prompts``; this
module is the cross-source listing + bundled detail.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from dashboard import projects as proj
from dashboard.settings import load_settings
from hammock.engine.runner import PROMPTS_DIR as BUNDLED_PROMPTS_DIR

log = logging.getLogger(__name__)

router = APIRouter()

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="name must be 1-64 chars, alphanumeric with `.`, `_`, `-`",
        )


def _entry(path: Path, source: str) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.stem,
        "source": source,
        "path": str(path),
        "size": stat.st_size,
        "modified_at": _dt.datetime.fromtimestamp(stat.st_mtime, tz=_dt.UTC).isoformat(),
    }


def _list_bundled() -> list[dict[str, Any]]:
    if not BUNDLED_PROMPTS_DIR.is_dir():
        return []
    return [_entry(p, "bundled") for p in sorted(BUNDLED_PROMPTS_DIR.glob("*.md"))]


def _list_project(slug: str, repo_path: Path) -> list[dict[str, Any]]:
    prompts_dir = repo_path / ".hammock-v2" / "prompts"
    if not prompts_dir.is_dir():
        return []
    return [_entry(p, slug) for p in sorted(prompts_dir.glob("*.md"))]


@router.get("/prompts")
def list_prompts(source: str | None = Query(default=None)) -> dict[str, Any]:
    """Aggregate list across bundled + every registered project.

    Optional ``source`` filter: ``"bundled"`` or a project slug. If
    omitted, returns the union.
    """
    settings = load_settings()
    out: list[dict[str, Any]] = []

    want_all = source is None
    want_bundled = want_all or source == "bundled"

    if want_bundled:
        out.extend(_list_bundled())

    if source and source != "bundled":
        try:
            slug = proj.normalize_slug(source)
        except proj.ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        data = proj.read_project(slug, settings.root)
        if data is None:
            raise HTTPException(status_code=404, detail=f"project {slug!r} not found")
        out.extend(_list_project(slug, Path(data["repo_path"])))
    elif want_all:
        for p in proj.list_projects(settings.root):
            slug = p["slug"]
            repo_path = Path(p.get("repo_path", ""))
            if str(repo_path):
                out.extend(_list_project(slug, repo_path))

    return {"prompts": out}


@router.get("/prompts/bundled")
def list_bundled_prompts() -> dict[str, Any]:
    return {"prompts": _list_bundled()}


@router.get("/prompts/bundled/{name}")
def get_bundled_prompt(name: str) -> dict[str, Any]:
    _validate_name(name)
    path = BUNDLED_PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"bundled prompt {name!r} not found")
    return {
        "name": name,
        "source": "bundled",
        "content": path.read_text(),
    }


__all__ = ["router"]
