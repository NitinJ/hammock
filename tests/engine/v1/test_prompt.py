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


def _setup_workflow_dir(
    tmp_path: Path, node_ids: list[str], *, content: str = "TASK INSTRUCTION FOR THIS NODE."
) -> Path:
    """Create a tmp workflow folder with stub prompt files for the given
    node ids. Returns the workflow_dir path that callers pass to
    build_prompt as `workflow_dir`."""
    wf_dir = tmp_path / "wf"
    (wf_dir / "prompts").mkdir(parents=True, exist_ok=True)
    for nid in node_ids:
        (wf_dir / "prompts" / f"{nid}.md").write_text(f"# {nid}\n\n{content}\n")
    return wf_dir


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
    wf_dir = _setup_workflow_dir(tmp_path, [node.id])
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=False,
            value=JobRequestValue(text="Fix the bug"),
            present=True,
        )
    }
    prompt = build_prompt(
        node=node,
        workflow=wf,
        inputs=inputs,
        job_dir=tmp_path,
        workflow_dir=wf_dir,
    )
    assert prompt.startswith("# Node: write-bug-report")


def test_build_prompt_inlines_input_via_render_for_consumer(tmp_path: Path) -> None:
    wf = _make_workflow_t1()
    node = wf.nodes[0]
    wf_dir = _setup_workflow_dir(tmp_path, [node.id])
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=False,
            value=JobRequestValue(text="Fix the empty-args bug"),
            present=True,
        )
    }
    prompt = build_prompt(
        node=node, workflow=wf, inputs=inputs, job_dir=tmp_path, workflow_dir=wf_dir
    )
    assert "Fix the empty-args bug" in prompt
    assert "(job-request)" in prompt


def test_build_prompt_describes_output_via_render_for_producer(tmp_path: Path) -> None:
    wf = _make_workflow_t1()
    node = wf.nodes[0]
    wf_dir = _setup_workflow_dir(tmp_path, [node.id])
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=False,
            value=JobRequestValue(text="x"),
            present=True,
        )
    }
    prompt = build_prompt(
        node=node, workflow=wf, inputs=inputs, job_dir=tmp_path, workflow_dir=wf_dir
    )
    assert "bug_report.json" in prompt
    assert "extra='forbid'" in prompt or "summary" in prompt


# ---------------------------------------------------------------------------
# build_prompt — optional input absent
# ---------------------------------------------------------------------------


def test_build_prompt_renders_absent_optional_input(tmp_path: Path) -> None:
    wf = _make_workflow_t1()
    node = wf.nodes[0]
    wf_dir = _setup_workflow_dir(tmp_path, [node.id])
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=True,
            value=None,
            present=False,
        )
    }
    prompt = build_prompt(
        node=node, workflow=wf, inputs=inputs, job_dir=tmp_path, workflow_dir=wf_dir
    )
    assert "optional, not produced" in prompt


# ---------------------------------------------------------------------------
# build_prompt — multiple inputs
# ---------------------------------------------------------------------------


def test_build_prompt_with_multiple_inputs(tmp_path: Path) -> None:
    wf = _make_workflow_t1()
    node = wf.nodes[1]  # write-design-spec consumes bug_report
    wf_dir = _setup_workflow_dir(tmp_path, [node.id])
    inputs = {
        "bug_report": ResolvedInput(
            name="bug_report",
            optional=False,
            value=BugReportValue(summary="the bug", document="## Bug\n\nthe bug body"),
            present=True,
        )
    }
    prompt = build_prompt(
        node=node, workflow=wf, inputs=inputs, job_dir=tmp_path, workflow_dir=wf_dir
    )
    assert "the bug" in prompt
    assert "design_spec.json" in prompt


# ---------------------------------------------------------------------------
# Stage 1 — prompts-as-files: middle layer loaded from disk
# ---------------------------------------------------------------------------


def test_build_prompt_inlines_middle_from_prompts_file(tmp_path: Path) -> None:
    """The middle layer of the assembled prompt comes from
    ``<workflow_dir>/prompts/<node_id>.md``. The file's contents must
    appear verbatim in the assembled prompt."""
    wf = _make_workflow_t1()
    node = wf.nodes[0]
    wf_dir = _setup_workflow_dir(tmp_path, [node.id], content="UNIQUE-MIDDLE-SENTINEL-ABCDEF")
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=False,
            value=JobRequestValue(text="x"),
            present=True,
        )
    }
    prompt = build_prompt(
        node=node, workflow=wf, inputs=inputs, job_dir=tmp_path, workflow_dir=wf_dir
    )
    assert "UNIQUE-MIDDLE-SENTINEL-ABCDEF" in prompt


def test_build_prompt_middle_appears_before_inputs(tmp_path: Path) -> None:
    """Layering order: header → middle → inputs → outputs.

    The middle's content must appear *before* the ``## Inputs`` heading
    so the agent reads the task instruction before scanning input
    sections."""
    wf = _make_workflow_t1()
    node = wf.nodes[0]
    wf_dir = _setup_workflow_dir(tmp_path, [node.id], content="MIDDLE-ORDER-SENTINEL-12345")
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=False,
            value=JobRequestValue(text="x"),
            present=True,
        )
    }
    prompt = build_prompt(
        node=node, workflow=wf, inputs=inputs, job_dir=tmp_path, workflow_dir=wf_dir
    )
    middle_idx = prompt.find("MIDDLE-ORDER-SENTINEL-12345")
    inputs_idx = prompt.find("## Inputs")
    assert middle_idx >= 0, "middle sentinel not found in assembled prompt"
    assert inputs_idx >= 0, "expected '## Inputs' heading not found"
    assert middle_idx < inputs_idx, (
        f"middle sentinel must appear before '## Inputs' "
        f"(middle at {middle_idx}, inputs at {inputs_idx})"
    )


def test_build_prompt_raises_when_middle_file_missing(tmp_path: Path) -> None:
    """Agent-actor nodes require a middle prompt file. Missing file at
    dispatch time is a hard error — verification should have caught it
    earlier, but the engine fails loudly rather than spawning claude
    with no task instruction."""
    wf = _make_workflow_t1()
    node = wf.nodes[0]
    # Set up workflow_dir but DO NOT create the prompt file for this node.
    wf_dir = tmp_path / "wf"
    (wf_dir / "prompts").mkdir(parents=True, exist_ok=True)
    inputs = {
        "request": ResolvedInput(
            name="request",
            optional=False,
            value=JobRequestValue(text="x"),
            present=True,
        )
    }
    with pytest.raises((FileNotFoundError, ValueError), match=node.id):
        build_prompt(
            node=node,
            workflow=wf,
            inputs=inputs,
            job_dir=tmp_path,
            workflow_dir=wf_dir,
        )
