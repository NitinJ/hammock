"""Unit tests for shared/v1/workflow.py — the YAML-loadable workflow model."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.v1.workflow import ArtifactNode, CodeNode, VariableSpec, Workflow

# ---------------------------------------------------------------------------
# VariableSpec
# ---------------------------------------------------------------------------


def test_variable_spec_minimal() -> None:
    spec = VariableSpec(type="bug-report")
    assert spec.type == "bug-report"


def test_variable_spec_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        VariableSpec(type="bug-report", schema="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ArtifactNode
# ---------------------------------------------------------------------------


def test_artifact_node_minimal() -> None:
    node = ArtifactNode(
        id="write-bug-report",
        kind="artifact",
        actor="agent",
        inputs={"request": "$job_request"},
        outputs={"bug_report": "$bug_report"},
    )
    assert node.id == "write-bug-report"
    assert node.kind == "artifact"
    assert node.actor == "agent"
    assert node.after == []
    assert node.runs_if is None


def test_artifact_node_actor_must_be_known() -> None:
    with pytest.raises(ValidationError):
        ArtifactNode(  # type: ignore[call-arg]
            id="x", kind="artifact", actor="robot", inputs={}, outputs={}
        )


def test_artifact_node_kind_must_be_artifact() -> None:
    with pytest.raises(ValidationError):
        ArtifactNode(  # type: ignore[arg-type]
            id="x", kind="code", actor="agent", inputs={}, outputs={}
        )


def test_artifact_node_with_after_edges() -> None:
    node = ArtifactNode(
        id="review-design-spec-agent",
        kind="artifact",
        actor="agent",
        after=["write-design-spec"],
        inputs={"design_spec": "$design_spec"},
        outputs={"verdict": "$design_spec_review_agent"},
    )
    assert node.after == ["write-design-spec"]


def test_artifact_node_with_runs_if() -> None:
    node = ArtifactNode(
        id="conditional-node",
        kind="artifact",
        actor="agent",
        runs_if="$some_var",
        inputs={},
        outputs={},
    )
    assert node.runs_if == "$some_var"


def test_artifact_node_optional_input_and_output_names() -> None:
    """Optional markers are part of the input/output NAME (e.g. `prior_review?`).
    The model stores them verbatim — semantic interpretation lives in the engine."""
    node = ArtifactNode(
        id="x",
        kind="artifact",
        actor="agent",
        inputs={"prior_review?": "$some_var"},
        outputs={"out?": "$some_output"},
    )
    assert "prior_review?" in node.inputs
    assert "out?" in node.outputs


def test_artifact_node_with_presentation() -> None:
    node = ArtifactNode(
        id="hil-gate",
        kind="artifact",
        actor="human",
        inputs={"design_spec": "$design_spec"},
        outputs={"verdict": "$verdict"},
        presentation={"title": "Review the design spec"},
    )
    assert node.presentation == {"title": "Review the design spec"}


def test_artifact_node_with_retries() -> None:
    node = ArtifactNode(
        id="x",
        kind="artifact",
        actor="agent",
        inputs={},
        outputs={},
        retries={"max": 2},
    )
    assert node.retries == {"max": 2}


def test_artifact_node_rejects_extra_top_level_fields() -> None:
    with pytest.raises(ValidationError):
        ArtifactNode(  # type: ignore[call-arg]
            id="x",
            kind="artifact",
            actor="agent",
            inputs={},
            outputs={},
            substrate="shared",  # not a valid field on artifact nodes
        )


# ---------------------------------------------------------------------------
# CodeNode
# ---------------------------------------------------------------------------


def test_code_node_minimal() -> None:
    node = CodeNode(
        id="implement",
        kind="code",
        actor="agent",
        inputs={"design_spec": "$design_spec"},
        outputs={"pr": "$pr"},
    )
    assert node.kind == "code"
    assert node.actor == "agent"


def test_code_node_kind_must_be_code() -> None:
    with pytest.raises(ValidationError):
        CodeNode(  # type: ignore[arg-type]
            id="x", kind="artifact", actor="agent", inputs={}, outputs={}
        )


def test_workflow_accepts_mixed_kinds() -> None:
    """A workflow YAML mixes `artifact` and `code` nodes; the discriminated
    union resolves each entry to the right model."""
    wf = Workflow.model_validate(
        {
            "workflow": "mix",
            "variables": {
                "spec": {"type": "design-spec"},
                "pr": {"type": "pr"},
            },
            "nodes": [
                {
                    "id": "write-spec",
                    "kind": "artifact",
                    "actor": "agent",
                    "inputs": {},
                    "outputs": {"spec": "$spec"},
                },
                {
                    "id": "implement",
                    "kind": "code",
                    "actor": "agent",
                    "after": ["write-spec"],
                    "inputs": {"spec": "$spec"},
                    "outputs": {"pr": "$pr"},
                },
            ],
        }
    )
    assert isinstance(wf.nodes[0], ArtifactNode)
    assert isinstance(wf.nodes[1], CodeNode)


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


def test_workflow_minimal() -> None:
    wf = Workflow(
        workflow="t1",
        variables={"x": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="n1",
                kind="artifact",
                actor="agent",
                inputs={},
                outputs={"x": "$x"},
            )
        ],
    )
    assert wf.workflow == "t1"
    assert "x" in wf.variables
    assert len(wf.nodes) == 1


def test_workflow_variables_is_keyed_dict() -> None:
    wf = Workflow(
        workflow="t1",
        variables={
            "bug_report": VariableSpec(type="bug-report"),
            "design_spec": VariableSpec(type="design-spec"),
        },
        nodes=[],
    )
    assert wf.variables["bug_report"].type == "bug-report"
    assert wf.variables["design_spec"].type == "design-spec"


def test_workflow_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Workflow(  # type: ignore[call-arg]
            workflow="t1", variables={}, nodes=[], unknown_field="x"
        )


def test_workflow_round_trip_through_dict() -> None:
    """Pydantic round-trip — load from dict, dump, re-load. Useful sanity
    check that YAML→dict→model→dict→model is stable."""
    payload = {
        "workflow": "t1-basic",
        "variables": {
            "request": {"type": "job-request"},
            "bug_report": {"type": "bug-report"},
        },
        "nodes": [
            {
                "id": "write-bug-report",
                "kind": "artifact",
                "actor": "agent",
                "after": [],
                "inputs": {"request": "$request"},
                "outputs": {"bug_report": "$bug_report"},
            }
        ],
    }
    wf = Workflow.model_validate(payload)
    dumped = wf.model_dump(exclude_none=True, exclude_defaults=False)
    wf2 = Workflow.model_validate(dumped)
    assert wf.workflow == wf2.workflow
    assert wf.variables.keys() == wf2.variables.keys()
    assert len(wf.nodes) == len(wf2.nodes)
    assert wf.nodes[0].id == wf2.nodes[0].id
