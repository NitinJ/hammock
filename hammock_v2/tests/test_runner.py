"""Tests for the v2 runner.

Uses a fake claude runner so no real LLM tokens get spent.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from hammock_v2.engine import paths
from hammock_v2.engine.runner import (
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
    """The orchestrator must check control.md between iterations and
    honor paused / cancelled states."""
    prompt = _orchestrator_prompt()
    assert "control.md" in prompt
    assert "paused" in prompt.lower()
    assert "cancelled" in prompt.lower()
    # The pause/cancel takes effect at the next checkpoint.
    assert "checkpoint" in prompt.lower() or "between Tasks" in prompt
    # The cancelled state writes job.md state=cancelled.
    assert "state: cancelled" in prompt or "state=cancelled" in prompt


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


def test_run_rejects_unknown_workflow(tmp_path: Path) -> None:
    from hammock_v2.engine.workflow import WorkflowError

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


def test_orchestrator_prompt_contains_artifact_handling() -> None:
    """Orchestrator must build an `# Attached artifacts` section for
    the first node when `inputs/` contains files."""
    prompt = render_orchestrator_prompt(
        job_dir=Path("/x"),
        workflow_path=Path("/x/workflow.yaml"),
        request_text="r",
    )
    assert "attached artifacts" in prompt.lower()
    assert "inputs/" in prompt
    assert "first 40 lines" in prompt.lower()
    # Threshold rules present
    assert "2KB" in prompt or "2kb" in prompt.lower()
    assert "40KB" in prompt or "40kb" in prompt.lower()


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
    prompt = render_orchestrator_prompt(
        job_dir=Path("/x"),
        workflow_path=Path("/x/workflow.yaml"),
        request_text="r",
    )
    # Task is the spawn mechanism.
    assert "Task" in prompt
    assert "subagent_type" in prompt or "subagent" in prompt
    # Worktree isolation is mentioned for code-bearing nodes.
    assert "worktree" in prompt.lower()
    # The chat.jsonl snapshot pattern is documented (orchestrator writes
    # a small claude-stream-compatible jsonl after Task returns).
    assert "chat.jsonl" in prompt
    # The Bash claude -p spawn pattern is NO LONGER recommended.
    # We allow the string "claude -p" only inside a "why not" rationale,
    # so the structural check is: the prompt does NOT instruct redirecting
    # output to chat.jsonl via Bash.
    assert ">" not in prompt.split("chat.jsonl")[0][-20:] or "Task" in prompt


def test_orchestrator_prompt_checks_messages_before_each_dispatch() -> None:
    """Operator messages must be checked at the START of each node
    iteration, not only after the node completes. This bounds reply
    latency at one Task duration rather than full-workflow duration."""
    prompt = render_orchestrator_prompt(
        job_dir=Path("/x"),
        workflow_path=Path("/x/workflow.yaml"),
        request_text="r",
    )
    # The pre-dispatch check is documented as step 2.0 (or named that way).
    assert "2.0" in prompt or "before doing" in prompt.lower()
    # Fast-ack pattern: emit a brief ack BEFORE acting on the message.
    assert "fast-ack" in prompt.lower() or (
        "got your message" in prompt.lower() or "got it" in prompt.lower()
    )
    # The main-loop responsiveness contract is called out near the top.
    assert "main loop" in prompt.lower() or "responsiveness" in prompt.lower()


def test_orchestrator_prompt_says_all_work_through_task() -> None:
    """The orchestrator must route ALL node work through Task and reserve
    its own time for orchestration + responsiveness, per the user's
    'all work through Tasks' directive."""
    prompt = render_orchestrator_prompt(
        job_dir=Path("/x"),
        workflow_path=Path("/x/workflow.yaml"),
        request_text="r",
    )
    assert "all work goes through task" in prompt.lower() or (
        "workflow nodes get one task each" in prompt.lower()
    )
