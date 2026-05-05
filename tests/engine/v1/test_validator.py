"""Unit tests for engine/v1/validator.py."""

from __future__ import annotations

import pytest

from engine.v1.validator import (
    WorkflowValidationError,
    assert_valid,
    validate,
)
from shared.v1.workflow import ArtifactNode, VariableSpec, Workflow


def _make_workflow(**kwargs: object) -> Workflow:
    defaults: dict[str, object] = {
        "workflow": "t",
        "variables": {},
        "nodes": [],
    }
    defaults.update(kwargs)
    return Workflow.model_validate(defaults)


# ---------------------------------------------------------------------------
# Variable type registration
# ---------------------------------------------------------------------------


def test_unknown_variable_type_is_finding() -> None:
    wf = _make_workflow(
        variables={"x": VariableSpec(type="not-a-real-type")},
    )
    findings = validate(wf)
    assert any("unknown type" in f.message for f in findings)


def test_known_types_pass() -> None:
    wf = _make_workflow(
        variables={
            "request": VariableSpec(type="job-request"),
            "bug_report": VariableSpec(type="bug-report"),
        },
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                inputs={"request": "$request"},
                outputs={"bug_report": "$bug_report"},
            ),
        ],
    )
    findings = validate(wf)
    type_findings = [f for f in findings if "unknown type" in f.message]
    assert type_findings == []


# ---------------------------------------------------------------------------
# Duplicate node ids
# ---------------------------------------------------------------------------


def test_duplicate_node_ids_flagged() -> None:
    wf = _make_workflow(
        variables={"x": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(id="n", kind="artifact", actor="agent", inputs={}, outputs={"o": "$x"}),
            ArtifactNode(id="n", kind="artifact", actor="agent", inputs={}, outputs={}),
        ],
    )
    findings = validate(wf)
    assert any("duplicate node id" in f.message for f in findings)


# ---------------------------------------------------------------------------
# `after:` references
# ---------------------------------------------------------------------------


def test_after_references_unknown_node_flagged() -> None:
    wf = _make_workflow(
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                after=["does-not-exist"],
                inputs={},
                outputs={},
            ),
        ],
    )
    findings = validate(wf)
    assert any("references unknown node" in f.message for f in findings)


def test_after_references_existing_passes() -> None:
    wf = _make_workflow(
        variables={"x": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="a", kind="artifact", actor="agent", inputs={}, outputs={"o": "$x"}
            ),
            ArtifactNode(
                id="b",
                kind="artifact",
                actor="agent",
                after=["a"],
                inputs={"i": "$x"},
                outputs={},
            ),
        ],
    )
    findings = validate(wf)
    assert all("references unknown node" not in f.message for f in findings)


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------


def test_simple_cycle_detected() -> None:
    wf = _make_workflow(
        nodes=[
            ArtifactNode(
                id="a",
                kind="artifact",
                actor="agent",
                after=["b"],
                inputs={},
                outputs={},
            ),
            ArtifactNode(
                id="b",
                kind="artifact",
                actor="agent",
                after=["a"],
                inputs={},
                outputs={},
            ),
        ],
    )
    findings = validate(wf)
    assert any("cycle detected" in f.message for f in findings)


def test_no_cycle_passes() -> None:
    wf = _make_workflow(
        variables={"x": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="a", kind="artifact", actor="agent", inputs={}, outputs={"o": "$x"}
            ),
            ArtifactNode(
                id="b",
                kind="artifact",
                actor="agent",
                after=["a"],
                inputs={"i": "$x"},
                outputs={},
            ),
        ],
    )
    findings = validate(wf)
    assert all("cycle" not in f.message for f in findings)


# ---------------------------------------------------------------------------
# Input / output references resolve
# ---------------------------------------------------------------------------


def test_undeclared_input_variable_flagged() -> None:
    wf = _make_workflow(
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                inputs={"x": "$nonexistent"},
                outputs={},
            ),
        ],
    )
    findings = validate(wf)
    assert any("undeclared variable" in f.message for f in findings)


def test_undeclared_output_variable_flagged() -> None:
    wf = _make_workflow(
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                inputs={},
                outputs={"x": "$ghost"},
            ),
        ],
    )
    findings = validate(wf)
    assert any("undeclared variable" in f.message for f in findings)


def test_malformed_input_reference_flagged() -> None:
    wf = _make_workflow(
        variables={"x": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                inputs={"i": "no-dollar-sign"},
                outputs={},
            ),
        ],
    )
    findings = validate(wf)
    assert any("malformed reference" in f.message for f in findings)


def test_field_access_reference_passes() -> None:
    """T1: `$variable.field` references the engine resolves at dispatch."""
    wf = _make_workflow(
        variables={"bug_report": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                inputs={"summary": "$bug_report.summary"},
                outputs={},
            ),
        ],
    )
    findings = validate(wf)
    # Only undeclared-variable / malformed findings would matter here.
    assert all(
        "undeclared variable" not in f.message and "malformed" not in f.message
        for f in findings
    )


# ---------------------------------------------------------------------------
# Single producer per variable
# ---------------------------------------------------------------------------


def test_two_producers_for_same_variable_flagged() -> None:
    wf = _make_workflow(
        variables={"bug_report": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="a",
                kind="artifact",
                actor="agent",
                inputs={},
                outputs={"o": "$bug_report"},
            ),
            ArtifactNode(
                id="b",
                kind="artifact",
                actor="agent",
                inputs={},
                outputs={"o": "$bug_report"},
            ),
        ],
    )
    findings = validate(wf)
    assert any("multiple producers" in f.message for f in findings)


# ---------------------------------------------------------------------------
# assert_valid hard-fail
# ---------------------------------------------------------------------------


def test_assert_valid_raises_on_findings() -> None:
    wf = _make_workflow(variables={"x": VariableSpec(type="bogus")})
    with pytest.raises(WorkflowValidationError):
        assert_valid(wf)


def test_assert_valid_passes_on_clean_workflow() -> None:
    wf = _make_workflow(
        variables={"bug_report": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                inputs={},
                outputs={"o": "$bug_report"},
            ),
        ],
    )
    assert_valid(wf)  # does not raise
