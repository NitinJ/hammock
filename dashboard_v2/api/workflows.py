"""Workflows CRUD: list / detail / create / update / delete.

Three-tier taxonomy:

- **bundled** — read-only, ships in ``hammock_v2/workflows/``.
- **custom** — user-created, cross-project, in ``<HAMMOCK_V2_ROOT>/workflows/``.
- **project-specific** — tied to one project, in
  ``<repo_path>/.hammock-v2/workflows/``.

This module exposes the global views (across all sources) and the
custom-tier mutations. Per-project workflows are managed in
``dashboard_v2.api.project_workflows``.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dashboard_v2 import workflows as wf_lib
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


# -------------------- Models --------------------


class WorkflowDetail(BaseModel):
    name: str
    description: str | None = None
    nodes: list[dict[str, Any]]
    yaml: str = Field(..., description="Raw YAML source.")
    bundled: bool
    source: str = Field(..., description="One of 'bundled', 'custom', or a project slug.")


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
    """Flat list across every source (bundled + custom + every
    registered project's project-specific workflows). Each entry has a
    `source` field. Names CAN duplicate across sources — the global
    list surfaces all of them so the operator sees what overrides what
    when used in a project context.
    """
    settings = load_settings()
    entries = wf_lib.list_all_for_workflows_screen(settings.root)
    return {"workflows": [e.to_dict() for e in entries]}


@router.get("/workflows/{name}")
def get_workflow(name: str) -> WorkflowDetail:
    """Lookup by name. Resolution priority for the global endpoint:
    custom > bundled. Project-specific copies are accessed via the
    per-project endpoint at ``/api/projects/:slug/workflows/:name``."""
    _validate_name(name)
    settings = load_settings()
    custom_path = wf_lib.resolve_for_source(name, wf_lib.SOURCE_CUSTOM, settings.root)
    if custom_path is not None:
        path = custom_path
        source = wf_lib.SOURCE_CUSTOM
    else:
        bundled_path = wf_lib.resolve_for_source(name, wf_lib.SOURCE_BUNDLED, settings.root)
        if bundled_path is None:
            raise HTTPException(status_code=404, detail=f"workflow {name!r} not found")
        path = bundled_path
        source = wf_lib.SOURCE_BUNDLED
    yaml_text = path.read_text()
    bundled = source == wf_lib.SOURCE_BUNDLED
    try:
        wf = load_workflow(path)
    except WorkflowError as exc:
        # We surface the raw yaml even when invalid so the editor can
        # render it for fixing.
        return WorkflowDetail(
            name=name,
            description=f"INVALID: {exc}",
            nodes=[],
            yaml=yaml_text,
            bundled=bundled,
            source=source,
        )
    summary = workflow_summary(wf)
    return WorkflowDetail(
        name=summary["name"],
        description=summary.get("description"),
        nodes=summary["nodes"],
        yaml=yaml_text,
        bundled=bundled,
        source=source,
    )


@router.post("/workflows", status_code=201)
def create_workflow(body: WorkflowCreateRequest) -> dict[str, Any]:
    """Create a user-custom workflow at
    ``<HAMMOCK_V2_ROOT>/workflows/<name>.yaml``. Bundled name conflict
    → 409 (cannot overwrite bundled). Custom name conflict → 409 (use
    PUT to update)."""
    _validate_name(body.name)
    settings = load_settings()
    bundled = BUNDLED_WORKFLOWS_DIR / f"{body.name}.yaml"
    if bundled.is_file():
        raise HTTPException(
            status_code=409,
            detail=f"a bundled workflow named {body.name!r} already exists",
        )
    user_dir = wf_lib.custom_workflows_dir(settings.root)
    target = user_dir / f"{body.name}.yaml"
    if target.is_file():
        raise HTTPException(
            status_code=409,
            detail=f"a custom workflow named {body.name!r} already exists; use PUT to update",
        )
    _validate_yaml_payload(body.yaml, expected_name=body.name)
    user_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(body.yaml)
    return {
        "name": body.name,
        "path": str(target),
        "source": wf_lib.SOURCE_CUSTOM,
        "bundled": False,
    }


@router.put("/workflows/{name}")
def update_workflow(name: str, body: WorkflowUpdateRequest) -> dict[str, Any]:
    """Update an existing user-custom workflow. Bundled name → 405."""
    _validate_name(name)
    settings = load_settings()
    bundled = BUNDLED_WORKFLOWS_DIR / f"{name}.yaml"
    if bundled.is_file():
        raise HTTPException(
            status_code=405,
            detail=f"workflow {name!r} is bundled and cannot be edited; save as a new name",
        )
    user_dir = wf_lib.custom_workflows_dir(settings.root)
    target = user_dir / f"{name}.yaml"
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"custom workflow {name!r} not found")
    _validate_yaml_payload(body.yaml, expected_name=name)
    target.write_text(body.yaml)
    return {"name": name, "path": str(target), "source": wf_lib.SOURCE_CUSTOM, "bundled": False}


@router.delete("/workflows/{name}")
def delete_workflow(name: str) -> dict[str, Any]:
    """Delete a user-custom workflow. Bundled → 405. Project-specific
    copies must be deleted via the per-project endpoint."""
    _validate_name(name)
    settings = load_settings()
    bundled = BUNDLED_WORKFLOWS_DIR / f"{name}.yaml"
    if bundled.is_file():
        raise HTTPException(
            status_code=405,
            detail=f"workflow {name!r} is bundled and cannot be deleted",
        )
    user_dir = wf_lib.custom_workflows_dir(settings.root)
    target = user_dir / f"{name}.yaml"
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"custom workflow {name!r} not found")
    target.unlink()
    return {"name": name, "deleted": True, "source": wf_lib.SOURCE_CUSTOM}


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
