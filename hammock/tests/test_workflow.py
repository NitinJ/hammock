"""Tests for the v2 workflow loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from hammock.engine.workflow import (
    Workflow,
    WorkflowError,
    load_workflow,
    topological_order,
    workflow_summary,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_load_minimal_workflow(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
""",
    )
    wf = load_workflow(p)
    assert wf.name == "t"
    assert [n.id for n in wf.nodes] == ["a"]
    assert wf.nodes[0].after == []
    assert wf.nodes[0].human_review is False


def test_load_dag_with_after(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
  - id: b
    prompt: bar
    after: [a]
""",
    )
    wf = load_workflow(p)
    order = [n.id for n in topological_order(wf)]
    assert order == ["a", "b"]


def test_load_human_review_flag(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
  - id: b
    prompt: review
    after: [a]
    human_review: true
""",
    )
    wf = load_workflow(p)
    assert wf.nodes[1].human_review is True


def test_workflow_summary_shape(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
description: d
nodes:
  - id: a
    prompt: foo
""",
    )
    wf = load_workflow(p)
    summary = workflow_summary(wf)
    assert summary["name"] == "t"
    assert summary["description"] == "d"
    assert summary["nodes"][0]["id"] == "a"


def test_load_rejects_unknown_after(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
    after: [nope]
""",
    )
    with pytest.raises(WorkflowError, match="unknown node"):
        load_workflow(p)


def test_load_rejects_duplicate_id(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
  - id: a
    prompt: bar
""",
    )
    with pytest.raises(WorkflowError, match="duplicate"):
        load_workflow(p)


def test_load_rejects_cycle(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
    after: [b]
  - id: b
    prompt: bar
    after: [a]
""",
    )
    with pytest.raises(WorkflowError, match="cycle"):
        load_workflow(p)


def test_load_rejects_extra_fields(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
    bogus: 1
""",
    )
    with pytest.raises(WorkflowError, match="bogus"):
        load_workflow(p)


def test_load_missing_file() -> None:
    with pytest.raises(WorkflowError, match="not found"):
        load_workflow(Path("/nonexistent/wf.yaml"))


def test_load_invalid_yaml(tmp_path: Path) -> None:
    p = _write(tmp_path / "wf.yaml", ":\n  - not valid")
    with pytest.raises(WorkflowError):
        load_workflow(p)


def test_bundled_fix_bug_loads() -> None:
    """The bundled fix-bug workflow validates."""
    here = Path(__file__).resolve().parent.parent / "workflows" / "fix-bug.yaml"
    wf = load_workflow(here)
    assert wf.name == "fix-bug"
    ids = [n.id for n in wf.nodes]
    assert "write-bug-report" in ids
    assert "open-pr" in ids
    assert "write-summary" in ids
    # human_review on the design review node
    by_id = {n.id: n for n in wf.nodes}
    assert by_id["review-design-spec"].human_review is True


def test_topological_order_branching(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
  - id: b
    prompt: foo
    after: [a]
  - id: c
    prompt: foo
    after: [a]
  - id: d
    prompt: foo
    after: [b, c]
""",
    )
    wf: Workflow = load_workflow(p)
    order = [n.id for n in topological_order(wf)]
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_requires_default_is_output_md(tmp_path: Path) -> None:
    """If a node doesn't declare `requires:`, default is ['output.md']."""
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
""",
    )
    wf = load_workflow(p)
    assert wf.nodes[0].requires == ["output.md"]


def test_requires_explicit_list(tmp_path: Path) -> None:
    """A node may declare additional required outputs."""
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
    requires: [output.md, branch.txt, summary.json]
""",
    )
    wf = load_workflow(p)
    assert wf.nodes[0].requires == ["output.md", "branch.txt", "summary.json"]


def test_requires_rejects_absolute_paths(tmp_path: Path) -> None:
    """Absolute paths in `requires:` are a security smell — refuse."""
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
    requires: [/etc/passwd]
""",
    )
    with pytest.raises(WorkflowError, match="must be a non-empty relative path"):
        load_workflow(p)


def test_requires_rejects_parent_dir_traversal(tmp_path: Path) -> None:
    """`..` in `requires:` is also rejected."""
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
    requires: [../../../etc/passwd]
""",
    )
    with pytest.raises(WorkflowError, match="must be a non-empty relative path"):
        load_workflow(p)


def test_requires_in_workflow_summary(tmp_path: Path) -> None:
    """workflow_summary surfaces the requires list to the dashboard."""
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
    requires: [output.md, branch.txt]
""",
    )
    wf = load_workflow(p)
    summary = workflow_summary(wf)
    assert summary["nodes"][0]["requires"] == ["output.md", "branch.txt"]


def test_bundled_fix_bug_implement_node_requires_branch_txt() -> None:
    """The bundled fix-bug workflow declares branch.txt for implement."""
    here = Path(__file__).resolve().parent.parent / "workflows" / "fix-bug.yaml"
    wf = load_workflow(here)
    impl = next(n for n in wf.nodes if n.id == "implement")
    assert "branch.txt" in impl.requires


# --- workflow_expander schema + validator -------------------------------


def test_node_default_kind_is_agent() -> None:
    from hammock.engine.workflow import Node

    n = Node(id="x", prompt="p")
    assert n.kind == "agent"


def test_workflow_expander_auto_adds_expansion_yaml_to_requires() -> None:
    from hammock.engine.workflow import Node

    n = Node(id="ex", prompt="ex", kind="workflow_expander")
    assert "expansion.yaml" in n.requires
    assert "output.md" in n.requires


def test_workflow_expander_rejects_worktree_true() -> None:
    from hammock.engine.workflow import Node

    with pytest.raises(Exception, match="cannot use worktree=true"):
        Node(id="ex", prompt="ex", kind="workflow_expander", worktree=True)


def test_validate_expansion_happy_path(tmp_path: Path) -> None:
    from hammock.engine.workflow import validate_expansion

    yaml_text = """
nodes:
  - id: task-a
    prompt: implement-task
  - id: task-b
    prompt: implement-task
  - id: checkpoint
    prompt: stage-checkpoint
    after: [task-a, task-b]
    human_review: true
"""
    nodes = validate_expansion(yaml_text, expander_id="execute-plan")
    assert [n.id for n in nodes] == ["task-a", "task-b", "checkpoint"]
    assert nodes[2].human_review is True


def test_validate_expansion_rejects_nested_expander() -> None:
    from hammock.engine.workflow import ExpansionError, validate_expansion

    yaml_text = """
nodes:
  - id: nested
    prompt: foo
    kind: workflow_expander
"""
    with pytest.raises(ExpansionError, match="nested workflow_expander not allowed"):
        validate_expansion(yaml_text, expander_id="ex")


def test_validate_expansion_rejects_dangling_after() -> None:
    from hammock.engine.workflow import ExpansionError, validate_expansion

    yaml_text = """
nodes:
  - id: a
    prompt: foo
  - id: b
    prompt: foo
    after: [does-not-exist]
"""
    with pytest.raises(ExpansionError, match="does not refer to another node"):
        validate_expansion(yaml_text, expander_id="ex")


def test_validate_expansion_rejects_duplicate_ids() -> None:
    from hammock.engine.workflow import ExpansionError, validate_expansion

    yaml_text = """
nodes:
  - id: dup
    prompt: foo
  - id: dup
    prompt: foo
"""
    with pytest.raises(ExpansionError, match="duplicate child id"):
        validate_expansion(yaml_text, expander_id="ex")


def test_validate_expansion_rejects_empty() -> None:
    from hammock.engine.workflow import ExpansionError, validate_expansion

    with pytest.raises(ExpansionError, match="empty"):
        validate_expansion("", expander_id="ex")


def test_validate_expansion_rejects_missing_nodes_key() -> None:
    from hammock.engine.workflow import ExpansionError, validate_expansion

    with pytest.raises(ExpansionError, match="non-empty 'nodes:' list"):
        validate_expansion("name: foo\n", expander_id="ex")


def test_validate_expansion_rejects_cycle() -> None:
    from hammock.engine.workflow import ExpansionError, validate_expansion

    yaml_text = """
nodes:
  - id: a
    prompt: foo
    after: [b]
  - id: b
    prompt: foo
    after: [a]
"""
    with pytest.raises(ExpansionError, match="cycle"):
        validate_expansion(yaml_text, expander_id="ex")


def test_prefix_expansion_ids_rewrites_after_edges() -> None:
    from hammock.engine.workflow import prefix_expansion_ids, validate_expansion

    yaml_text = """
nodes:
  - id: task-a
    prompt: implement-task
  - id: checkpoint
    prompt: stage-checkpoint
    after: [task-a]
"""
    nodes = validate_expansion(yaml_text, expander_id="execute-plan")
    prefixed = prefix_expansion_ids(nodes, expander_id="execute-plan")
    assert [n.id for n in prefixed] == [
        "execute-plan__task-a",
        "execute-plan__checkpoint",
    ]
    assert prefixed[1].after == ["execute-plan__task-a"]
    # Originals unchanged
    assert nodes[0].id == "task-a"
    assert nodes[1].after == ["task-a"]


def test_bundled_stage_implementation_workflow_loads() -> None:
    """The bundled stage-implementation workflow validates and exposes the
    workflow_expander node correctly."""
    here = Path(__file__).resolve().parent.parent / "workflows" / "stage-implementation.yaml"
    wf = load_workflow(here)
    by_id = {n.id: n for n in wf.nodes}
    assert "read-plan" in by_id
    assert "execute-plan" in by_id
    assert "write-summary" in by_id
    assert by_id["read-plan"].kind == "agent"
    assert by_id["execute-plan"].kind == "workflow_expander"
    assert "expansion.yaml" in by_id["execute-plan"].requires
    assert by_id["execute-plan"].worktree is False


def test_workflow_summary_exposes_kind(tmp_path: Path) -> None:
    """Summary projection includes the new kind field for the dashboard."""
    p = _write(
        tmp_path / "wf.yaml",
        """
name: t
nodes:
  - id: a
    prompt: foo
  - id: b
    prompt: bar
    kind: workflow_expander
""",
    )
    wf = load_workflow(p)
    summary = workflow_summary(wf)
    kinds = {n["id"]: n["kind"] for n in summary["nodes"]}
    assert kinds == {"a": "agent", "b": "workflow_expander"}
