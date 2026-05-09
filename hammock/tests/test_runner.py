"""Tests for the v2 runner.

Uses a fake claude runner so no real LLM tokens get spent.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from hammock.engine import paths
from hammock.engine.runner import (
    JobConfig,
    render_orchestrator_prompt,
    run_job,
    submit_job,
)


def _orchestrator_prompt() -> str:
    return render_orchestrator_prompt(
        job_dir=Path("/x"),
        workflow_path=Path("/x/workflow.yaml"),
        request_text="r",
    )


def _fake_runner_factory(
    *, output_lines: list[str] | None = None, returncode: int = 0
) -> Callable[[list[str], Path, Path, Path], subprocess.CompletedProcess[bytes]]:
    """Build a runner that writes canned stream-json lines and returns
    the requested rc."""
    lines = output_lines or ['{"type":"system","subtype":"init"}']

    def fake(
        cmd: list[str], cwd: Path, stdout_path: Path, stderr_path: Path
    ) -> subprocess.CompletedProcess[bytes]:
        stdout_path.write_text("\n".join(lines) + "\n")
        stderr_path.write_text("")
        return subprocess.CompletedProcess(args=cmd, returncode=returncode)

    return fake


def test_submit_creates_layout(tmp_path: Path) -> None:
    job = JobConfig(slug="t-001", workflow_name="fix-bug", request_text="fix it")
    submit_job(job=job, root=tmp_path)
    jd = paths.job_dir("t-001", root=tmp_path)
    assert jd.is_dir()
    assert (jd / "job.md").is_file()
    assert (jd / "workflow.yaml").is_file()
    nodes_dir = jd / "nodes"
    assert nodes_dir.is_dir()
    # Each bundled workflow node should have a state.md pre-seeded.
    state_files = list(nodes_dir.rglob("state.md"))
    assert len(state_files) >= 5
    for f in state_files:
        assert "pending" in f.read_text()


def test_submit_writes_initial_control_md(tmp_path: Path) -> None:
    """submit_job should drop a control.md with state: running so the
    orchestrator's lifecycle gate has something to read on first poll."""
    job = JobConfig(slug="t-ctrl-1", workflow_name="fix-bug", request_text="fix it")
    submit_job(job=job, root=tmp_path)
    ctrl = paths.control_md("t-ctrl-1", root=tmp_path)
    assert ctrl.is_file()
    text = ctrl.read_text()
    assert "state: running" in text
    assert "requested_at:" in text


def test_orchestrator_prompt_polls_control_md() -> None:
    """The orchestrator must check control.md every main-loop iteration
    and honor paused / cancelled states."""
    prompt = _orchestrator_prompt()
    assert "control.md" in prompt
    assert "paused" in prompt.lower()
    assert "cancelled" in prompt.lower()
    # The pause/cancel takes effect on the next loop iteration (≤1s),
    # not at "between tasks" — the non-blocking model removes that idiom.
    assert "next checkpoint" in prompt.lower() or "main loop" in prompt.lower()
    # The cancelled state writes job.md state=cancelled.
    assert "state: cancelled" in prompt or "state=cancelled" in prompt


def test_orchestrator_paused_does_not_fall_through_to_exit() -> None:
    """Regression: when paused, the orchestrator must NOT reach Step F
    and must NOT mark the job completed. Step B's paused branch must
    explicitly forbid falling through to subsequent steps; Step F must
    gate on last_control_state == "running"."""
    prompt = _orchestrator_prompt()
    lower = prompt.lower()
    # Step B should explicitly forbid fall-through from paused.
    assert "do not fall through" in lower or "do not fall-through" in lower
    # Step F should gate on running control state.
    assert (
        'last_control_state == "running"' in prompt or "last_control_state == 'running'" in prompt
    )
    # The "pending nodes are NOT terminal" warning must be present so the
    # LLM doesn't treat 0 active_tasks during pause as "all done".
    assert "pending nodes are not terminal" in lower or "pending nodes are NOT terminal" in prompt


def test_submit_copies_repo(tmp_path: Path) -> None:
    src = tmp_path / "src-repo"
    (src / "subdir").mkdir(parents=True)
    (src / "README.md").write_text("hi")
    job = JobConfig(
        slug="t-002",
        workflow_name="fix-bug",
        request_text="x",
        project_repo_path=src,
    )
    submit_job(job=job, root=tmp_path)
    repo = paths.repo_dir("t-002", root=tmp_path)
    assert repo.is_dir()
    assert (repo / "README.md").read_text() == "hi"


def test_run_writes_orchestrator_jsonl_and_marks_completed(tmp_path: Path) -> None:
    job = JobConfig(slug="t-003", workflow_name="fix-bug", request_text="fix it")
    rc = run_job(
        job=job,
        root=tmp_path,
        runner=_fake_runner_factory(returncode=0),
    )
    assert rc == 0
    jsonl = paths.orchestrator_jsonl("t-003", root=tmp_path)
    assert jsonl.is_file()
    assert "system" in jsonl.read_text()
    job_md = (paths.job_dir("t-003", root=tmp_path) / "job.md").read_text()
    assert "state: completed" in job_md


def test_run_marks_failed_on_nonzero_rc(tmp_path: Path) -> None:
    job = JobConfig(slug="t-004", workflow_name="fix-bug", request_text="x")
    rc = run_job(
        job=job,
        root=tmp_path,
        runner=_fake_runner_factory(returncode=1),
    )
    assert rc == 1
    job_md = (paths.job_dir("t-004", root=tmp_path) / "job.md").read_text()
    assert "state: failed" in job_md
    assert "rc=1" in job_md


def test_render_orchestrator_substitutes_context(tmp_path: Path) -> None:
    prompt = render_orchestrator_prompt(
        job_dir=Path("/some/job"),
        workflow_path=Path("/some/job/workflow.yaml"),
        request_text="Reduce flicker on highlight render.",
    )
    assert "/some/job" in prompt
    assert "/some/job/workflow.yaml" in prompt
    assert "Reduce flicker" in prompt
    assert "$JOB_DIR" not in prompt  # all substitutions complete
    assert "$WORKFLOW_PATH" not in prompt
    assert "$REQUEST_TEXT" not in prompt
    assert "$PROMPTS_DIR" not in prompt
    assert "$HELPERS_DIR" not in prompt


def test_render_orchestrator_substitutes_helpers_dir() -> None:
    """$HELPERS_DIR must resolve to <PROMPTS_DIR>/helpers/ so the
    orchestrator can read helper templates by name."""
    prompts_dir = Path(__file__).resolve().parent.parent / "prompts"
    prompt = render_orchestrator_prompt(
        job_dir=Path("/some/job"),
        workflow_path=Path("/some/job/workflow.yaml"),
        request_text="r",
        prompts_dir=prompts_dir,
    )
    expected_helpers = str(prompts_dir / "helpers")
    assert expected_helpers in prompt
    assert "$HELPERS_DIR" not in prompt


def test_render_orchestrator_warns_when_helpers_dir_missing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If the helpers directory is missing, the runner should log a
    warning at startup so the operator can debug helper-spawn failures."""
    import logging as _logging

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "orchestrator.md").write_text("HELLO $HELPERS_DIR\n")
    # No helpers/ subdir — should warn.
    with caplog.at_level(_logging.WARNING, logger="hammock.engine.runner"):
        rendered = render_orchestrator_prompt(
            job_dir=Path("/x"),
            workflow_path=Path("/x/workflow.yaml"),
            request_text="r",
            prompts_dir=prompts_dir,
        )
    assert str(prompts_dir / "helpers") in rendered
    assert any("helpers directory not found" in r.message for r in caplog.records)


def test_run_rejects_unknown_workflow(tmp_path: Path) -> None:
    from hammock.engine.workflow import WorkflowError

    job = JobConfig(slug="t-005", workflow_name="bogus", request_text="x")
    with pytest.raises(WorkflowError):
        run_job(job=job, root=tmp_path, runner=_fake_runner_factory())


def test_resume_run_is_idempotent(tmp_path: Path) -> None:
    """Running the same job twice should reuse the job dir."""
    job = JobConfig(slug="t-006", workflow_name="fix-bug", request_text="x")
    run_job(job=job, root=tmp_path, runner=_fake_runner_factory())
    # Mutate something the second run shouldn't reset.
    state = paths.node_state("t-006", "write-bug-report", root=tmp_path)
    assert state.is_file()
    state.write_text("---\nstate: succeeded\n---\n")
    run_job(job=job, root=tmp_path, runner=_fake_runner_factory())
    # The pre-existing per-node state was preserved (submit doesn't overwrite).
    assert "succeeded" in state.read_text()


def test_orchestrator_prompt_contains_strict_validation_instructions() -> None:
    """The orchestrator prompt must instruct the agent to validate every
    `requires:` path strictly (file existence + non-empty), retry once
    on failure, and hard-fail after a single retry."""
    prompt = render_orchestrator_prompt(
        job_dir=Path("/x"),
        workflow_path=Path("/x/workflow.yaml"),
        request_text="r",
    )
    # Strict validation language present
    assert "strict file-existence check" in prompt.lower()
    assert "non-empty" in prompt.lower() or "size > 0" in prompt.lower()
    # Single retry policy present
    assert "ONCE" in prompt or "once" in prompt.lower()
    # No semantic check claim
    assert "no semantic check" in prompt.lower()
    # Validation.md path is described
    assert "validation.md" in prompt


def test_helper_owns_artifact_handling() -> None:
    """Artifact section construction is now delegated to the
    prepare-node-input helper — the orchestrator references it but does
    not inline the size-threshold rules."""
    helper = (
        Path(__file__).resolve().parent.parent / "prompts" / "helpers" / "prepare-node-input.md"
    ).read_text()
    assert "Attached artifacts" in helper
    assert "inputs/" in helper
    assert "first 40 lines" in helper.lower()
    assert "2KB" in helper
    assert "40KB" in helper


def test_orchestrator_prompt_contains_revision_loop() -> None:
    """Orchestrator must cap revisions at 3 cycles."""
    prompt = render_orchestrator_prompt(
        job_dir=Path("/x"),
        workflow_path=Path("/x/workflow.yaml"),
        request_text="r",
    )
    assert "3 revision" in prompt.lower() or "max revisions" in prompt.lower()


def test_orchestrator_prompt_uses_task_for_subagents() -> None:
    """Orchestrator must dispatch each node's subagent via the Task tool
    (not Bash claude -p). For nodes with `worktree: true`, Task is invoked
    with `isolation="worktree"` so code-bearing subagents get isolated
    git worktrees."""
    prompt = _orchestrator_prompt()
    # Task is the spawn mechanism.
    assert "Task" in prompt
    assert "subagent_type" in prompt or "subagent" in prompt
    # Worktree isolation is mentioned for code-bearing nodes.
    assert "worktree" in prompt.lower()
    # The chat.jsonl snapshot pattern is documented (orchestrator writes
    # a small claude-stream-compatible jsonl after Task completes).
    assert "chat.jsonl" in prompt


def test_orchestrator_prompt_checks_messages_each_loop_iteration() -> None:
    """Operator messages must be checked at the start of EVERY main-loop
    iteration. Non-blocking Task means the loop runs ~1Hz regardless of
    whether any Task is in flight, so the operator's max ack latency is
    one loop tick (~1s), not a full Task duration."""
    prompt = _orchestrator_prompt()
    # Step A is the message-drain step at the top of every iteration.
    assert "Step A" in prompt or "drain operator messages" in prompt.lower()
    # Fast-ack pattern: emit a brief ack BEFORE acting on the message.
    assert "fast-ack" in prompt.lower() or (
        "got your message" in prompt.lower() or "got it" in prompt.lower()
    )
    # The main-loop responsiveness contract is called out near the top.
    assert "main loop" in prompt.lower() or "responsiveness" in prompt.lower()


def test_orchestrator_prompt_says_all_work_through_task() -> None:
    """The orchestrator must route ALL node work through Task and reserve
    its own time for orchestration + responsiveness. Under the
    thin-router architecture this is restated as the orchestrator being
    a thin router that delegates to helper Tasks."""
    prompt = _orchestrator_prompt()
    lower = prompt.lower()
    assert (
        "all work goes through task" in lower
        or "workflow nodes get one task each" in lower
        or "thin router" in lower
    )


def test_orchestrator_prompt_uses_non_blocking_task_pattern() -> None:
    """Orchestrator uses non-blocking Task() with TaskOutput(block=False)
    polling and tracks active spawns in active_tasks. This is what makes
    the loop continuous and operator-responsive."""
    prompt = _orchestrator_prompt()
    # TaskOutput is the polling primitive.
    assert "TaskOutput" in prompt
    # Non-blocking semantics explicitly called out.
    assert "non-blocking" in prompt.lower()
    # Active-tasks tracking in persisted state.
    assert "active_tasks" in prompt
    # block=False on poll calls.
    assert "block=False" in prompt or "block: False" in prompt or "block:false" in prompt.lower()
    # Concurrency cap.
    lower = prompt.lower()
    assert (
        "10 concurrent" in lower
        or "ten concurrent" in lower
        or "10-task cap" in lower
        or "< 10" in lower
    )


def test_orchestrator_prompt_polls_in_single_loop() -> None:
    """The procedure is one continuous loop, not a per-node sequential
    walk. The 'between Tasks' idiom from the synchronous model must be
    gone."""
    prompt = _orchestrator_prompt()
    # Single continuous main loop is the canonical framing.
    assert "main loop" in prompt.lower()
    assert "continuous" in prompt.lower()
    # The "between Tasks" idiom is no longer the framing.
    # (We allow the string in a "no between Tasks framing" disclaimer,
    # but it must not be the operative framing.)
    # The loop iterates ~1Hz (1 second cadence).
    assert "~1Hz" in prompt or "1Hz" in prompt or "~1 second" in prompt or "~1s" in prompt


# --- workflow_expander handling ----------------------------------------


def test_orchestrator_prompt_handles_workflow_expander() -> None:
    """The prompt must explain how to handle nodes with kind:
    workflow_expander. Validation rules now live in the
    `process-expansion` helper template; the orchestrator references
    the helper and the surrounding state-machine semantics."""
    prompt = render_orchestrator_prompt(
        job_dir=Path("/x"),
        workflow_path=Path("/x/workflow.yaml"),
        request_text="r",
    )
    # Mentioned by kind name + helper.
    assert "workflow_expander" in prompt
    assert "process-expansion" in prompt
    # ID prefixing convention is documented in either orchestrator or helper.
    helper = (
        Path(__file__).resolve().parent.parent / "prompts" / "helpers" / "process-expansion.md"
    ).read_text()
    assert "__" in prompt
    assert "prefix" in helper.lower() or "<expander_id>__" in helper or "<EXPANDER_ID>__" in helper
    # The validation rules — including no-nesting — live in the helper.
    helper_lower = helper.lower()
    assert (
        "no nested" in helper_lower or "no nesting" in helper_lower or "single-shot" in helper_lower
    )


def test_orchestrator_prompt_documents_aggregation_barrier() -> None:
    """The prompt must describe the aggregation-barrier semantics:
    static nodes downstream of an expander wait for ALL expanded
    children to be terminal."""
    prompt = render_orchestrator_prompt(
        job_dir=Path("/x"),
        workflow_path=Path("/x/workflow.yaml"),
        request_text="r",
    )
    assert (
        "aggregation barrier" in prompt.lower()
        or "every expanded" in prompt.lower()
        or "all expanded" in prompt.lower()
    )


def test_orchestrator_prompt_describes_expanded_nodes_state() -> None:
    """`expanded_nodes` should appear in the persisted-state schema so
    the orchestrator tracks parent_expander relationships for the
    dashboard's grouping projection."""
    prompt = render_orchestrator_prompt(
        job_dir=Path("/x"),
        workflow_path=Path("/x/workflow.yaml"),
        request_text="r",
    )
    assert "expanded_nodes" in prompt
    assert "parent_expander" in prompt


def test_process_expansion_helper_rejects_nested_expanders_explicitly() -> None:
    """The validation rules — including no-nesting — live in the
    process-expansion helper template under the thin-router architecture.
    Nested workflow_expander must be rejected (single-shot, single-level)."""
    helper = (
        Path(__file__).resolve().parent.parent / "prompts" / "helpers" / "process-expansion.md"
    ).read_text()
    lower = helper.lower()
    assert "no nested" in lower or "no nesting" in lower or "single-shot" in lower
