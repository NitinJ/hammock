"""Project-scoped workflow listing — Stage 5.

Returns the union of bundled workflows + the project's local workflows
under ``<repo_path>/.hammock/workflows/``. Each entry carries a
``valid`` flag and an ``error`` reason when the workflow fails
verification (missing prompt files, malformed yaml, unsupported
``schema_version``).

The submit form's dropdown reads from this endpoint when the operator
has selected a project; the engine compile path resolves project-local
before bundled (see ``dashboard/compiler/compile.py``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from engine.v1.loader import WorkflowLoadError, load_workflow
from shared.v1.workflow import ArtifactNode, CodeNode, LoopNode, Workflow

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------


class ProjectWorkflowItem(BaseModel):
    """One entry in ``GET /api/projects/{slug}/workflows``."""

    model_config = ConfigDict(extra="forbid")

    job_type: str
    """Workflow folder name. Used as the submit identifier."""

    workflow_name: str | None
    """The yaml's ``workflow:`` field. ``None`` when the yaml failed
    to load (so we have no name to show)."""

    source: str  # "bundled" | "custom"
    valid: bool
    error: str | None = None
    """Verification error, when ``valid is False``. Names the file
    path or the missing prompt(s) so the operator can fix in their
    editor."""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


_BUNDLED_DIR = Path(__file__).parent.parent.parent / "hammock" / "templates" / "workflows"


def _agent_actor_node_ids(workflow: Workflow) -> list[str]:
    """Walk the DAG (including loop bodies) and collect every node
    whose ``actor == 'agent'`` — these are the nodes that need a
    ``prompts/<id>.md`` file."""
    out: list[str] = []

    def visit(nodes: list[ArtifactNode | CodeNode | LoopNode]) -> None:
        for n in nodes:
            if isinstance(n, LoopNode):
                visit(n.body)
                continue
            # n is ArtifactNode | CodeNode here (LoopNode handled above).
            if n.actor == "agent":
                out.append(n.id)

    visit(workflow.nodes)
    return out


def verify_workflow_folder(folder: Path) -> tuple[Workflow | None, str | None]:
    """Validate a single workflow folder.

    Returns ``(workflow, None)`` on success, ``(None, error_reason)`` or
    ``(workflow, error_reason)`` on failure (workflow returned when
    yaml loaded but prompts are missing — caller may still want the
    name).
    """
    yaml_path = folder / "workflow.yaml"
    if not yaml_path.is_file():
        return None, f"missing workflow.yaml in {folder}"
    try:
        wf = load_workflow(yaml_path)
    except WorkflowLoadError as exc:
        return None, str(exc)

    prompts_dir = folder / "prompts"
    missing: list[str] = []
    for node_id in _agent_actor_node_ids(wf):
        if not (prompts_dir / f"{node_id}.md").is_file():
            missing.append(node_id)
    if missing:
        return wf, (
            f"missing prompts for agent-actor node(s) {missing}: expected "
            f"{prompts_dir}/<node_id>.md"
        )
    return wf, None


def _list_bundled() -> list[ProjectWorkflowItem]:
    """Enumerate ``hammock/templates/workflows/<name>/workflow.yaml``."""
    items: list[ProjectWorkflowItem] = []
    if not _BUNDLED_DIR.is_dir():
        return items
    for folder in sorted(p for p in _BUNDLED_DIR.iterdir() if p.is_dir()):
        wf, error = verify_workflow_folder(folder)
        items.append(
            ProjectWorkflowItem(
                job_type=folder.name,
                workflow_name=wf.workflow if wf is not None else None,
                source="bundled",
                valid=error is None,
                error=error,
            )
        )
    return items


def _list_project_local(repo_path: Path) -> list[ProjectWorkflowItem]:
    """Enumerate ``<repo_path>/.hammock/workflows/<name>/workflow.yaml``."""
    items: list[ProjectWorkflowItem] = []
    workflows_dir = repo_path / ".hammock" / "workflows"
    if not workflows_dir.is_dir():
        return items
    for folder in sorted(p for p in workflows_dir.iterdir() if p.is_dir()):
        wf, error = verify_workflow_folder(folder)
        items.append(
            ProjectWorkflowItem(
                job_type=folder.name,
                workflow_name=wf.workflow if wf is not None else None,
                source="custom",
                valid=error is None,
                error=error,
            )
        )
    return items


def list_workflows_for_project(root: Path, project_slug: str) -> list[ProjectWorkflowItem] | None:
    """Return bundled + project-local for the given project, or
    ``None`` if the project does not exist.

    Custom workflows are listed first so they shadow bundled entries
    sharing the same ``job_type`` (the compile path resolves
    project-local before bundled — same precedence here for UI
    consistency).
    """
    pj = root / "projects" / project_slug / "project.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        log.warning("project %s has malformed project.json: %s", project_slug, exc)
        return None
    repo_path = Path(data.get("repo_path", ""))

    custom = _list_project_local(repo_path)
    bundled = _list_bundled()

    # Dedupe: custom wins over bundled when job_type collides.
    custom_names = {c.job_type for c in custom}
    merged = custom + [b for b in bundled if b.job_type not in custom_names]
    return merged


def resolve_project_local_workflow(repo_path: Path, job_type: str) -> Path | None:
    """Return the workflow.yaml path inside ``repo_path/.hammock/`` for
    a given ``job_type``, or ``None`` if no such project-local
    workflow exists. Used by the compile path to prefer project-local
    over bundled."""
    candidate = repo_path / ".hammock" / "workflows" / job_type / "workflow.yaml"
    return candidate if candidate.is_file() else None


def resolve_bundled_source(name: str) -> Path | None:
    """Return the bundled workflow folder for ``name``, or ``None`` if
    no such bundled workflow exists. Used by the copy endpoint."""
    candidate = _BUNDLED_DIR / name
    return candidate if (candidate / "workflow.yaml").is_file() else None


def project_repo_path(root: Path, project_slug: str) -> Path | None:
    """Return the registered project's ``repo_path`` from project.json,
    or ``None`` if the project does not exist or its record is malformed."""
    pj = root / "projects" / project_slug / "project.json"
    if not pj.is_file():
        return None
    try:
        data = json.loads(pj.read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    rp = data.get("repo_path")
    if not isinstance(rp, str):
        return None
    return Path(rp)
