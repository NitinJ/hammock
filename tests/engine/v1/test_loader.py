"""Unit tests for engine/v1/loader.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.v1.loader import WorkflowLoadError, load_workflow


def test_load_minimal_workflow(tmp_path: Path) -> None:
    yaml_path = tmp_path / "wf.yaml"
    yaml_path.write_text(
        """
workflow: t1
variables:
  bug_report: { type: bug-report }
nodes:
  - id: write-bug-report
    kind: artifact
    actor: agent
    inputs: {}
    outputs: { bug_report: $bug_report }
"""
    )
    wf = load_workflow(yaml_path)
    assert wf.workflow == "t1"
    assert "bug_report" in wf.variables
    assert len(wf.nodes) == 1


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(WorkflowLoadError, match="not found"):
        load_workflow(tmp_path / "nope.yaml")


def test_load_malformed_yaml_raises(tmp_path: Path) -> None:
    yaml_path = tmp_path / "wf.yaml"
    yaml_path.write_text("workflow: [unbalanced\n")
    with pytest.raises(WorkflowLoadError, match="parse error"):
        load_workflow(yaml_path)


def test_load_non_mapping_top_level_raises(tmp_path: Path) -> None:
    yaml_path = tmp_path / "wf.yaml"
    yaml_path.write_text("- just a list\n")
    with pytest.raises(WorkflowLoadError, match="top-level mapping"):
        load_workflow(yaml_path)


def test_load_schema_validation_error_raises(tmp_path: Path) -> None:
    yaml_path = tmp_path / "wf.yaml"
    yaml_path.write_text("workflow: t1\nnodes: not-a-list\n")
    with pytest.raises(WorkflowLoadError, match="schema validation failed"):
        load_workflow(yaml_path)
