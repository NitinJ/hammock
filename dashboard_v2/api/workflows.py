"""Workflows CRUD: list / detail / create / update / delete.

Bundled workflows live in ``hammock_v2/workflows/`` and are read-only.
User-defined workflows live in ``<HAMMOCK_V2_ROOT>/workflows/`` and may
be edited or deleted.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dashboard_v2.settings import load_settings
from hammock_v2.engine.runner import WORKFLOWS_DIR as BUNDLED_WORKFLOWS_DIR
from hammock_v2.engine.workflow import (
    WorkflowError,
    load_workflow,
    workflow_summary,
)

log = logging.getLogger(__name__)

router = APIRouter()

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def _user_workflows_dir(root: Path) -> Path:
    return root / "workflows"


def _resolve_workflow_path(name: str, root: Path) -> tuple[Path, bool] | None:
    """Return (path, is_bundled). User-defined wins over bundled when names clash."""
    user_path = _user_workflows_dir(root) / f"{name}.yaml"
    if user_path.is_file():
        return user_path, False
    bundled = BUNDLED_WORKFLOWS_DIR / f"{name}.yaml"
    if bundled.is_file():
        return bundled, True
    return None


def _list_all(root: Path) -> list[tuple[str, Path, bool]]:
    """Return (name, path, is_bundled) for every workflow we know."""
    out: dict[str, tuple[str, Path, bool]] = {}
    if BUNDLED_WORKFLOWS_DIR.is_dir():
        for p in sorted(BUNDLED_WORKFLOWS_DIR.glob("*.yaml")):
            name = p.stem
            out[name] = (name, p, True)
    user_dir = _user_workflows_dir(root)
    if user_dir.is_dir():
        for p in sorted(user_dir.glob("*.yaml")):
            name = p.stem
            out[name] = (name, p, False)
    return list(out.values())


# -------------------- Models --------------------


class WorkflowDetail(BaseModel):
    name: str
    description: str | None = None
    nodes: list[dict[str, Any]]
    yaml: str = Field(..., description="Raw YAML source.")
    bundled: bool


class WorkflowCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    yaml: str = Field(..., min_length=1)


class WorkflowUpdateRequest(BaseModel):
    yaml: str = Field(..., min_length=1)


# -------------------- Helpers --------------------


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail=(
                "workflow name must be 1-64 chars, alphanumeric with `.`, `_`, `-`; "
                "must start with an alphanumeric char"
            ),
        )


def _validate_yaml_payload(yaml_text: str, expected_name: str | None = None) -> None:
    """Parse + DAG-check the yaml. Raises HTTPException(400) on failure."""
    try:
        # load_workflow needs a Path; write to a tmp file.
        from tempfile import NamedTemporaryFile

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


# -------------------- Endpoints --------------------


@router.get("/workflows")
def list_workflows() -> dict[str, Any]:
    settings = load_settings()
    out: list[dict[str, Any]] = []
    for name, path, bundled in _list_all(settings.root):
        try:
            wf = load_workflow(path)
        except WorkflowError as exc:
            log.warning("workflow %s failed to load: %s", path, exc)
            out.append({"name": name, "bundled": bundled, "error": str(exc)})
            continue
        summary = workflow_summary(wf)
        summary["bundled"] = bundled
        out.append(summary)
    return {"workflows": out}


@router.get("/workflows/{name}")
def get_workflow(name: str) -> WorkflowDetail:
    _validate_name(name)
    settings = load_settings()
    resolved = _resolve_workflow_path(name, settings.root)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"workflow {name!r} not found")
    path, bundled = resolved
    yaml_text = path.read_text()
    try:
        wf = load_workflow(path)
    except WorkflowError as exc:
        # We surface the raw yaml even when invalid so the editor can
        # render it for fixing.
        return WorkflowDetail(
            name=name,
            description=None,
            nodes=[],
            yaml=yaml_text,
            bundled=bundled,
        ).model_copy(update={"description": f"INVALID: {exc}"})
    summary = workflow_summary(wf)
    return WorkflowDetail(
        name=summary["name"],
        description=summary.get("description"),
        nodes=summary["nodes"],
        yaml=yaml_text,
        bundled=bundled,
    )


@router.post("/workflows", status_code=201)
def create_workflow(body: WorkflowCreateRequest) -> dict[str, Any]:
    _validate_name(body.name)
    settings = load_settings()
    bundled = BUNDLED_WORKFLOWS_DIR / f"{body.name}.yaml"
    if bundled.is_file():
        raise HTTPException(
            status_code=409,
            detail=f"a bundled workflow named {body.name!r} already exists",
        )
    user_dir = _user_workflows_dir(settings.root)
    target = user_dir / f"{body.name}.yaml"
    if target.is_file():
        raise HTTPException(
            status_code=409,
            detail=f"a user workflow named {body.name!r} already exists; use PUT to update",
        )
    _validate_yaml_payload(body.yaml, expected_name=body.name)
    user_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(body.yaml)
    return {"name": body.name, "path": str(target), "bundled": False}


@router.put("/workflows/{name}")
def update_workflow(name: str, body: WorkflowUpdateRequest) -> dict[str, Any]:
    _validate_name(name)
    settings = load_settings()
    bundled = BUNDLED_WORKFLOWS_DIR / f"{name}.yaml"
    if bundled.is_file():
        raise HTTPException(
            status_code=405,
            detail=f"workflow {name!r} is bundled and cannot be edited; save as a new name",
        )
    user_dir = _user_workflows_dir(settings.root)
    target = user_dir / f"{name}.yaml"
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"user workflow {name!r} not found")
    _validate_yaml_payload(body.yaml, expected_name=name)
    target.write_text(body.yaml)
    return {"name": name, "path": str(target), "bundled": False}


@router.delete("/workflows/{name}")
def delete_workflow(name: str) -> dict[str, Any]:
    _validate_name(name)
    settings = load_settings()
    bundled = BUNDLED_WORKFLOWS_DIR / f"{name}.yaml"
    if bundled.is_file():
        raise HTTPException(
            status_code=405,
            detail=f"workflow {name!r} is bundled and cannot be deleted",
        )
    user_dir = _user_workflows_dir(settings.root)
    target = user_dir / f"{name}.yaml"
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"user workflow {name!r} not found")
    target.unlink()
    return {"name": name, "deleted": True}


@router.post("/workflows/validate")
def validate_workflow_yaml(body: WorkflowUpdateRequest) -> dict[str, Any]:
    """Validate yaml without saving. Used by the live editor; also
    returns the parsed node summary so the editor can render the DAG
    preview without bundling a YAML parser in the SPA."""
    from tempfile import NamedTemporaryFile

    try:
        with NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as tmp:
            tmp.write(body.yaml)
            tmp_path = Path(tmp.name)
        try:
            wf = load_workflow(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    except WorkflowError as exc:
        return {"valid": False, "error": f"workflow validation: {exc}"}
    summary = workflow_summary(wf)
    return {
        "valid": True,
        "name": summary["name"],
        "description": summary.get("description"),
        "nodes": summary["nodes"],
    }


__all__ = ["router"]
