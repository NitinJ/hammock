"""Three-tier workflow taxonomy.

Hammock v2 workflows can live in three places:

1. **bundled** — read-only, ships in ``hammock/workflows/*.yaml``.
   Available to every project.
2. **custom** — user-created, cross-project. Lives at
   ``<HAMMOCK_ROOT>/workflows/<name>.yaml``. Editable. Available to
   every project.
3. **<project_slug>** — tied to one specific project. Lives at
   ``<repo_path>/.hammock-v2/workflows/<name>.yaml``. Editable. Visible
   only when that project is in the picker (or in the global list, with
   the project slug as the source label).

Resolution priority at job-submit (when a project is selected):
project-specific > custom > bundled (later shadows earlier with the same
name).

This module is the single source of truth for the taxonomy. The API
modules call into here.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dashboard import projects as proj
from hammock.engine.runner import WORKFLOWS_DIR as BUNDLED_WORKFLOWS_DIR
from hammock.engine.workflow import (
    WorkflowError,
    load_workflow,
    workflow_summary,
)

log = logging.getLogger(__name__)


# -------------------- Source-name constants --------------------


SOURCE_BUNDLED = "bundled"
SOURCE_CUSTOM = "custom"


# -------------------- Path helpers --------------------


def custom_workflows_dir(root: Path) -> Path:
    """User-custom (cross-project) workflows live here."""
    return root / "workflows"


def project_workflows_dir(repo_path: Path) -> Path:
    """Per-project workflows live here."""
    return repo_path / ".hammock-v2" / "workflows"


# -------------------- Entry shape --------------------


@dataclass
class WorkflowEntry:
    """A single workflow as listed in the dashboard.

    `source` is one of:
      - ``"bundled"`` — ships with hammock
      - ``"custom"`` — under ``<HAMMOCK_ROOT>/workflows/``
      - any project slug — under that project's repo
    """

    name: str
    source: str
    path: Path
    description: str | None
    nodes: list[dict[str, Any]]
    modified_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "path": str(self.path),
            "description": self.description,
            "nodes": self.nodes,
            "node_count": len(self.nodes),
            "modified_at": self.modified_at,
            # Back-compat: the old API exposed `bundled: bool`. Keep it
            # so existing tests + frontend paths keep working until they
            # migrate to `source`.
            "bundled": self.source == SOURCE_BUNDLED,
        }


def _mtime_iso(path: Path) -> str | None:
    try:
        return _dt.datetime.fromtimestamp(path.stat().st_mtime, tz=_dt.UTC).isoformat()
    except OSError:
        return None


def _load_entry(path: Path, source: str) -> WorkflowEntry | None:
    """Load + normalize a yaml file into a WorkflowEntry. None on parse fail."""
    try:
        wf = load_workflow(path)
    except WorkflowError as exc:
        log.warning("workflow %s failed to load: %s", path, exc)
        return None
    summary = workflow_summary(wf)
    return WorkflowEntry(
        name=summary["name"],
        source=source,
        path=path,
        description=summary.get("description"),
        nodes=summary["nodes"],
        modified_at=_mtime_iso(path),
    )


# -------------------- Per-source listings --------------------


def list_bundled() -> list[WorkflowEntry]:
    out: list[WorkflowEntry] = []
    if not BUNDLED_WORKFLOWS_DIR.is_dir():
        return out
    for p in sorted(BUNDLED_WORKFLOWS_DIR.glob("*.yaml")):
        entry = _load_entry(p, SOURCE_BUNDLED)
        if entry is not None:
            out.append(entry)
    return out


def list_user_custom(root: Path) -> list[WorkflowEntry]:
    out: list[WorkflowEntry] = []
    user_dir = custom_workflows_dir(root)
    if not user_dir.is_dir():
        return out
    for p in sorted(user_dir.glob("*.yaml")):
        entry = _load_entry(p, SOURCE_CUSTOM)
        if entry is not None:
            out.append(entry)
    return out


def list_project_specific(project_slug: str, repo_path: Path) -> list[WorkflowEntry]:
    out: list[WorkflowEntry] = []
    pdir = project_workflows_dir(repo_path)
    if not pdir.is_dir():
        return out
    for p in sorted(pdir.glob("*.yaml")):
        entry = _load_entry(p, project_slug)
        if entry is not None:
            out.append(entry)
    return out


# -------------------- Aggregate listings --------------------


def list_all_for_workflows_screen(root: Path) -> list[WorkflowEntry]:
    """Every workflow we know about, across every source.

    Order: bundled, then custom, then project-specific (alphabetical by
    project slug). Names CAN duplicate across sources — the global list
    surfaces all of them so the operator sees what overrides what.
    """
    out: list[WorkflowEntry] = []
    out.extend(list_bundled())
    out.extend(list_user_custom(root))
    for p in proj.list_projects(root):
        slug = p.get("slug")
        repo_path_str = p.get("repo_path")
        if not slug or not repo_path_str:
            continue
        out.extend(list_project_specific(slug, Path(repo_path_str)))
    return out


def list_for_project(project_slug: str | None, root: Path) -> list[WorkflowEntry]:
    """Workflows usable for a specific project: project-specific +
    custom + bundled, with project-specific shadowing custom shadowing
    bundled (when names collide).

    When ``project_slug`` is None: returns bundled + custom only.
    """
    by_name: dict[str, WorkflowEntry] = {}
    for entry in list_bundled():
        by_name[entry.name] = entry
    for entry in list_user_custom(root):
        by_name[entry.name] = entry
    if project_slug is not None:
        project = proj.read_project(project_slug, root)
        if project is not None:
            repo_path = Path(project["repo_path"])
            for entry in list_project_specific(project_slug, repo_path):
                by_name[entry.name] = entry
    return list(by_name.values())


# -------------------- Resolution --------------------


def resolve_at_submit(name: str, root: Path, project_slug: str | None) -> Path | None:
    """Resolve to a yaml path for job-submit.

    Priority (high → low): project-specific > custom > bundled.
    Returns None if the workflow can't be found at any tier.
    """
    if project_slug is not None:
        project = proj.read_project(project_slug, root)
        if project is not None:
            repo_path = Path(project["repo_path"])
            candidate = project_workflows_dir(repo_path) / f"{name}.yaml"
            if candidate.is_file():
                return candidate
    custom_path = custom_workflows_dir(root) / f"{name}.yaml"
    if custom_path.is_file():
        return custom_path
    bundled = BUNDLED_WORKFLOWS_DIR / f"{name}.yaml"
    if bundled.is_file():
        return bundled
    return None


def resolve_for_source(name: str, source: str, root: Path) -> Path | None:
    """Resolve a workflow at a specific source. Used by GET / PUT /
    DELETE endpoints when the client knows which copy they want.

    `source` is either ``"bundled"``, ``"custom"``, or a project slug.
    """
    if source == SOURCE_BUNDLED:
        path = BUNDLED_WORKFLOWS_DIR / f"{name}.yaml"
        return path if path.is_file() else None
    if source == SOURCE_CUSTOM:
        path = custom_workflows_dir(root) / f"{name}.yaml"
        return path if path.is_file() else None
    # otherwise interpret as project slug
    project = proj.read_project(source, root)
    if project is None:
        return None
    repo_path = Path(project["repo_path"])
    path = project_workflows_dir(repo_path) / f"{name}.yaml"
    return path if path.is_file() else None


__all__ = [
    "SOURCE_BUNDLED",
    "SOURCE_CUSTOM",
    "WorkflowEntry",
    "custom_workflows_dir",
    "list_all_for_workflows_screen",
    "list_bundled",
    "list_for_project",
    "list_project_specific",
    "list_user_custom",
    "project_workflows_dir",
    "resolve_at_submit",
    "resolve_for_source",
]
