"""Stage 1 — bundled prompts as files.

Per `docs/hammock-workflow.md`, every bundled workflow lives as a folder
under ``hammock/templates/workflows/<name>/`` containing a
``workflow.yaml`` and a sibling ``prompts/`` directory with one ``.md``
file per agent-actor node.

These integration tests assert that contract on the actual bundled
workflows (``fix-bug`` and ``t1-basic``):

- The folder layout exists.
- Every agent-actor node has a non-empty ``prompts/<node_id>.md`` file.
- Loader can load the workflow.yaml from the folder.

Failure here means a bundled workflow shipped without its prompts —
the dashboard would surface it as broken at job-submit time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.v1.loader import load_workflow
from shared.v1.workflow import ArtifactNode, CodeNode, LoopNode, Workflow

BUNDLED_WORKFLOWS_DIR = Path(__file__).parent.parent.parent / "hammock" / "templates" / "workflows"


def _bundled_workflow_dirs() -> list[Path]:
    """Return every immediate subdirectory of bundled workflows root that
    contains a ``workflow.yaml``."""
    if not BUNDLED_WORKFLOWS_DIR.is_dir():
        return []
    return sorted(p for p in BUNDLED_WORKFLOWS_DIR.iterdir() if (p / "workflow.yaml").is_file())


def _all_agent_actor_nodes(workflow: Workflow) -> list[ArtifactNode | CodeNode]:
    """Walk the DAG (including loop bodies) and collect every node whose
    ``actor == 'agent'`` — these are the nodes that need prompts."""
    out: list[ArtifactNode | CodeNode] = []

    def visit(nodes: list) -> None:
        for n in nodes:
            if isinstance(n, LoopNode):
                visit(n.body)
                continue
            if isinstance(n, ArtifactNode | CodeNode) and n.actor == "agent":
                out.append(n)

    visit(workflow.nodes)
    return out


def test_bundled_workflows_use_folder_layout() -> None:
    """Every bundled workflow ships as ``<name>/workflow.yaml``, not a
    flat ``<name>.yaml`` file. Stage 1 commits to the folder convention
    so Stage 5 (project-local discovery) reuses the same shape."""
    dirs = _bundled_workflow_dirs()
    assert dirs, (
        "no bundled workflows found under "
        f"{BUNDLED_WORKFLOWS_DIR} — Stage 1 expects folder layout "
        "(<name>/workflow.yaml + prompts/<id>.md)"
    )
    # No flat *.yaml files at the root — they must move into folders.
    flat_yamls = sorted(BUNDLED_WORKFLOWS_DIR.glob("*.yaml"))
    assert not flat_yamls, (
        f"flat yaml files found: {flat_yamls}; bundled workflows must "
        "live in folders (<name>/workflow.yaml)"
    )


def test_fix_bug_workflow_present() -> None:
    """The bundled fix-bug workflow specifically must exist — T1-T6 e2e
    runs against it."""
    fix_bug_dir = BUNDLED_WORKFLOWS_DIR / "fix-bug"
    assert (fix_bug_dir / "workflow.yaml").is_file(), (
        "fix-bug bundled workflow missing — T1-T6 e2e suite depends on it"
    )


@pytest.mark.parametrize("workflow_dir", _bundled_workflow_dirs(), ids=lambda p: p.name)
def test_bundled_workflow_loads(workflow_dir: Path) -> None:
    """Loader can load each bundled workflow's workflow.yaml without
    error — no syntax regressions during the move from flat to folder."""
    wf = load_workflow(workflow_dir / "workflow.yaml")
    assert wf.workflow, "loaded workflow has empty `workflow:` name"
    assert wf.nodes, "loaded workflow has no nodes"


@pytest.mark.parametrize("workflow_dir", _bundled_workflow_dirs(), ids=lambda p: p.name)
def test_bundled_workflow_has_prompt_per_agent_node(workflow_dir: Path) -> None:
    """For every agent-actor node — in artifact, code, and loop-body
    positions — the workflow's ``prompts/<node_id>.md`` file exists and
    is non-empty.

    A missing file means the engine has nothing to put in the middle
    layer of the prompt; verification should reject the workflow at
    submit time."""
    wf = load_workflow(workflow_dir / "workflow.yaml")
    prompts_dir = workflow_dir / "prompts"
    assert prompts_dir.is_dir(), (
        f"workflow {workflow_dir.name!r} has no prompts/ directory — expected at {prompts_dir}"
    )

    missing: list[str] = []
    empty: list[str] = []
    for node in _all_agent_actor_nodes(wf):
        prompt_file = prompts_dir / f"{node.id}.md"
        if not prompt_file.is_file():
            missing.append(node.id)
            continue
        content = prompt_file.read_text().strip()
        if not content:
            empty.append(node.id)

    assert not missing, (
        f"workflow {workflow_dir.name!r} missing prompt files for "
        f"agent-actor nodes: {missing} (expected at "
        f"{prompts_dir}/<node_id>.md)"
    )
    assert not empty, f"workflow {workflow_dir.name!r} has empty prompt files for: {empty}"
