"""Per-project workflows + prompts CRUD.

Workflows are listed from three tiers (bundled, custom, this project).
Project-specific shadows custom shadows bundled when names collide. The
picker on the job-submit form uses this listing.

Prompts: bundled (``hammock_v2/prompts/``) + project-local under
``<repo_path>/.hammock-v2/prompts/<name>.md``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dashboard_v2 import projects as proj
from dashboard_v2 import workflows as wf_lib
from dashboard_v2.settings import load_settings
from hammock_v2.engine.runner import (
    PROMPTS_DIR as BUNDLED_PROMPTS_DIR,
)
from hammock_v2.engine.runner import (
    WORKFLOWS_DIR as BUNDLED_WORKFLOWS_DIR,
)
from hammock_v2.engine.workflow import (
    WorkflowError,
    load_workflow,
    workflow_summary,
)

log = logging.getLogger(__name__)

router = APIRouter()

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="name must be 1-64 chars, alphanumeric with `.`, `_`, `-`",
        )


def _project_or_404(slug: str) -> tuple[str, Path]:
    settings = load_settings()
    try:
        slug = proj.normalize_slug(slug)
    except proj.ProjectError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = proj.read_project(slug, settings.root)
    if data is None:
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")
    return slug, Path(data["repo_path"])


def _project_workflows_dir(repo_path: Path) -> Path:
    return repo_path / ".hammock-v2" / "workflows"


def _project_prompts_dir(repo_path: Path) -> Path:
    return repo_path / ".hammock-v2" / "prompts"


# -------------------- Workflows --------------------


class WorkflowBody(BaseModel):
    name: str = Field(..., min_length=1)
    yaml: str = Field(..., min_length=1)


class WorkflowUpdateBody(BaseModel):
    yaml: str = Field(..., min_length=1)


def _validate_yaml(yaml_text: str, expected_name: str | None = None) -> None:
    try:
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
            tmp.write(yaml_text)
            tmp_path = Path(tmp.name)
        try:
            wf = load_workflow(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    except WorkflowError as exc:
        raise HTTPException(status_code=400, detail=f"workflow validation: {exc}") from exc
    if expected_name is not None and wf.name != expected_name:
        raise HTTPException(
            status_code=400,
            detail=f"workflow yaml `name:` ({wf.name!r}) does not match url path ({expected_name!r})",
        )


@router.get("/projects/{slug}/workflows")
def list_project_workflows(slug: str) -> dict[str, Any]:
    """Bundled + custom + project-specific. Shadowing applied:
    project-specific > custom > bundled when names collide.

    Each entry carries `source` (bundled / custom / <project_slug>) so
    the picker can show a pill. `bundled` field retained for back-compat.
    """
    project_slug, _repo_path = _project_or_404(slug)
    settings = load_settings()
    entries = wf_lib.list_for_project(project_slug, settings.root)
    return {"workflows": [e.to_dict() for e in entries]}


@router.post("/projects/{slug}/workflows", status_code=201)
def create_project_workflow(slug: str, body: WorkflowBody) -> dict[str, Any]:
    _validate_name(body.name)
    _, repo_path = _project_or_404(slug)
    user_dir = _project_workflows_dir(repo_path)
    user_dir.mkdir(parents=True, exist_ok=True)
    target = user_dir / f"{body.name}.yaml"
    if target.is_file():
        raise HTTPException(
            status_code=409,
            detail=f"project workflow {body.name!r} already exists; use PUT to update",
        )
    _validate_yaml(body.yaml, expected_name=body.name)
    target.write_text(body.yaml)
    return {"name": body.name, "path": str(target)}


@router.put("/projects/{slug}/workflows/{name}")
def update_project_workflow(slug: str, name: str, body: WorkflowUpdateBody) -> dict[str, Any]:
    _validate_name(name)
    _, repo_path = _project_or_404(slug)
    target = _project_workflows_dir(repo_path) / f"{name}.yaml"
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"project workflow {name!r} not found")
    _validate_yaml(body.yaml, expected_name=name)
    target.write_text(body.yaml)
    return {"name": name, "path": str(target)}


@router.delete("/projects/{slug}/workflows/{name}")
def delete_project_workflow(slug: str, name: str) -> dict[str, Any]:
    _validate_name(name)
    _, repo_path = _project_or_404(slug)
    target = _project_workflows_dir(repo_path) / f"{name}.yaml"
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"project workflow {name!r} not found")
    target.unlink()
    return {"name": name, "deleted": True}


@router.get("/projects/{slug}/workflows/{name}")
def get_project_workflow(slug: str, name: str) -> dict[str, Any]:
    """Resolve in project context: project-specific > custom > bundled.

    The returned `source` field tells the caller which tier the yaml
    came from.
    """
    _validate_name(name)
    project_slug, repo_path = _project_or_404(slug)
    settings = load_settings()
    # Walk priorities in order
    project_path = wf_lib.project_workflows_dir(repo_path) / f"{name}.yaml"
    if project_path.is_file():
        path = project_path
        source = project_slug
        bundled = False
    elif (custom_path := wf_lib.custom_workflows_dir(settings.root) / f"{name}.yaml").is_file():
        path = custom_path
        source = wf_lib.SOURCE_CUSTOM
        bundled = False
    elif (bundled_path := BUNDLED_WORKFLOWS_DIR / f"{name}.yaml").is_file():
        path = bundled_path
        source = wf_lib.SOURCE_BUNDLED
        bundled = True
    else:
        raise HTTPException(status_code=404, detail=f"workflow {name!r} not found")
    yaml_text = path.read_text()
    try:
        wf = load_workflow(path)
    except WorkflowError as exc:
        return {
            "name": name,
            "description": f"INVALID: {exc}",
            "nodes": [],
            "yaml": yaml_text,
            "bundled": bundled,
            "source": source,
        }
    summary = workflow_summary(wf)
    return {
        "name": summary["name"],
        "description": summary.get("description"),
        "nodes": summary["nodes"],
        "yaml": yaml_text,
        "bundled": bundled,
        "source": source,
    }


# -------------------- Prompts --------------------


class PromptBody(BaseModel):
    name: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)


@router.get("/projects/{slug}/prompts")
def list_project_prompts(slug: str) -> dict[str, Any]:
    _, repo_path = _project_or_404(slug)
    out: dict[str, dict[str, Any]] = {}
    if BUNDLED_PROMPTS_DIR.is_dir():
        for p in sorted(BUNDLED_PROMPTS_DIR.glob("*.md")):
            out[p.stem] = {"name": p.stem, "bundled": True}
    user_dir = _project_prompts_dir(repo_path)
    if user_dir.is_dir():
        for p in sorted(user_dir.glob("*.md")):
            out[p.stem] = {"name": p.stem, "bundled": False}
    return {"prompts": list(out.values())}


@router.get("/projects/{slug}/prompts/{name}")
def get_project_prompt(slug: str, name: str) -> dict[str, Any]:
    _validate_name(name)
    _, repo_path = _project_or_404(slug)
    user_path = _project_prompts_dir(repo_path) / f"{name}.md"
    if user_path.is_file():
        return {"name": name, "content": user_path.read_text(), "bundled": False}
    bundled_path = BUNDLED_PROMPTS_DIR / f"{name}.md"
    if bundled_path.is_file():
        return {"name": name, "content": bundled_path.read_text(), "bundled": True}
    raise HTTPException(status_code=404, detail=f"prompt {name!r} not found")


@router.post("/projects/{slug}/prompts", status_code=201)
def save_project_prompt(slug: str, body: PromptBody) -> dict[str, Any]:
    _validate_name(body.name)
    _, repo_path = _project_or_404(slug)
    user_dir = _project_prompts_dir(repo_path)
    user_dir.mkdir(parents=True, exist_ok=True)
    target = user_dir / f"{body.name}.md"
    target.write_text(body.content)
    return {"name": body.name, "path": str(target)}


@router.delete("/projects/{slug}/prompts/{name}")
def delete_project_prompt(slug: str, name: str) -> dict[str, Any]:
    _validate_name(name)
    _, repo_path = _project_or_404(slug)
    target = _project_prompts_dir(repo_path) / f"{name}.md"
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"project prompt {name!r} not found")
    target.unlink()
    return {"name": name, "deleted": True}


__all__ = ["router"]
