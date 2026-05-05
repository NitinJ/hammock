"""Unit tests for engine/v1/prompt.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.v1.prompt import build_prompt, collect_output_slots
from engine.v1.resolver import ResolvedInput
from shared.v1.types.bug_report import BugReportValue
from shared.v1.types.job_request import JobRequestValue
from shared.v1.workflow import ArtifactNode, VariableSpec, Workflow


def _make_workflow_t1() -> Workflow:
    return Workflow(
        workflow="t1",
        variables={
            "request": VariableSpec(type="job-request"),
            "bug_report": VariableSpec(type="bug-report"),
            "design_spec": VariableSpec(type="design-spec"),
        },
        nodes=[
            ArtifactNode(
                id="write-bug-report",
                kind="artifact",
                actor="agent",
                inputs={"request": "$request"},
                outputs={"bug_report": "$bug_report"},
            ),
            ArtifactNode(
                id="write-design-spec",
                kind="artifact",
                actor="agent",
                after=["write-bug-report"],
                inputs={"bug_report": "$bug_report"},
                outputs={"design_spec": "$design_spec"},
            ),
        ],
    )


# ---------------------------------------------------------------------------
# collect_output_slots
# ---------------------------------------------------------------------------


def test_collect_output_slots_basic() -> None:
    wf = _make_workflow_t1()
    slots = collect_output_slots(wf.nodes[0], wf)
    assert len(slots) == 1
    assert slots[0].slot_name == "bug_report"
    assert slots[0].var_name == "bug_report"
    assert slots[0].type_name == "bug-report"
    assert slots[0].optional is False


def test_collect_output_slots_optional() -> None:
    wf = Workflow(
        workflow="t",
        variables={"x": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                inputs={},
                outputs={"x?": "$x"},
            )
        ],
    )
    slots = collect_output_slots(wf.nodes[0], wf)
    assert slots[0].optional is True
    assert slots[0].slot_name == "x"


def test_collect_output_slots_undeclared_variable_raises() -> None:
    """Validator should have caught this earlier; build_prompt is downstream
    of the validator, so it raises directly rather than producing a useless
    prompt."""
    wf = Workflow(
        workflow="t",
        variables={},
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                inputs={},
                outputs={"x": "$ghost"},
            )
        ],
    )
    with pytest.raises(ValueError, match="undeclared"):
        collect_output_slots(wf.nodes[0], wf)


# ---------------------------------------------------------------------------
# build_prompt — happy path with one input + one output
# ---------------------------------------------------------------------------


def test_build_prompt_includes_node_header(tmp_path: Path) -> None:
    wf = _make_workflow_t1()
    node = wf.nodes[0]
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=False,
            value=JobRequestValue(text="Fix the bug"),
            present=True,
        )
    }
    prompt = build_prompt(node=node, workflow=wf, inputs=inputs, job_dir=tmp_path)
    assert prompt.startswith("# Node: write-bug-report")


def test_build_prompt_inlines_input_via_render_for_consumer(tmp_path: Path) -> None:
    wf = _make_workflow_t1()
    node = wf.nodes[0]
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=False,
            value=JobRequestValue(text="Fix the empty-args bug"),
            present=True,
        )
    }
    prompt = build_prompt(node=node, workflow=wf, inputs=inputs, job_dir=tmp_path)
    # JobRequestType.render_for_consumer produces a fragment containing the
    # variable name + the request text; both should appear verbatim.
    assert "Fix the empty-args bug" in prompt
    assert "(job-request)" in prompt


def test_build_prompt_describes_output_via_render_for_producer(tmp_path: Path) -> None:
    wf = _make_workflow_t1()
    node = wf.nodes[0]
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=False,
            value=JobRequestValue(text="x"),
            present=True,
        )
    }
    prompt = build_prompt(node=node, workflow=wf, inputs=inputs, job_dir=tmp_path)
    # BugReportType.render_for_producer mentions the file path the agent
    # must write to AND the schema hint.
    assert "bug_report.json" in prompt
    assert "extra='forbid'" in prompt or "summary" in prompt


# ---------------------------------------------------------------------------
# build_prompt — optional input absent
# ---------------------------------------------------------------------------


def test_build_prompt_renders_absent_optional_input(tmp_path: Path) -> None:
    wf = _make_workflow_t1()
    node = wf.nodes[0]
    # Pretend `request` is optional and not present.
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=True,
            value=None,
            present=False,
        )
    }
    prompt = build_prompt(node=node, workflow=wf, inputs=inputs, job_dir=tmp_path)
    assert "optional, not produced" in prompt


# ---------------------------------------------------------------------------
# build_prompt — multiple inputs
# ---------------------------------------------------------------------------


def test_build_prompt_with_multiple_inputs(tmp_path: Path) -> None:
    wf = _make_workflow_t1()
    node = wf.nodes[1]  # write-design-spec consumes bug_report
    inputs = {
        "bug_report": ResolvedInput(
            name="bug_report",
            optional=False,
            value=BugReportValue(summary="the bug"),
            present=True,
        )
    }
    prompt = build_prompt(node=node, workflow=wf, inputs=inputs, job_dir=tmp_path)
    assert "the bug" in prompt
    assert "design_spec.json" in prompt
