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
