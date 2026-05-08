"""Tests for the v2 workflow loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from hammock_v2.engine.workflow import (
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
