"""Unit tests for engine/v1/driver.py.

Run end-to-end with a fake claude runner so the driver path is exercised
without spawning Claude. Covers: topological order, state transitions,
node persistence, failure handling.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from engine.v1.driver import (
    DriverError,
    JobSubmissionError,
    _topological_order,
    run_job,
    submit_job,
)
from shared.v1 import paths
from shared.v1.envelope import Envelope
from shared.v1.job import JobConfig, JobState, NodeRun, NodeRunState
from shared.v1.workflow import ArtifactNode, VariableSpec, Workflow


def _t1_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "t1.yaml"
    p.write_text(
        """
workflow: t1
variables:
  request: { type: job-request }
  bug_report: { type: bug-report }
  design_spec: { type: design-spec }
nodes:
  - id: write-bug-report
    kind: artifact
    actor: agent
    inputs: { request: $request }
    outputs: { bug_report: $bug_report }
  - id: write-design-spec
    kind: artifact
    actor: agent
    after: [write-bug-report]
    inputs: { bug_report: $bug_report }
    outputs: { design_spec: $design_spec }
"""
    )
    _seed_prompts(tmp_path, ["write-bug-report", "write-design-spec"])
    return p


def _seed_prompts(workflow_dir: Path, node_ids: list[str]) -> None:
    """Write a stub prompt file for each agent-actor node id alongside
    the synthetic workflow yaml. Stage 1 makes per-node prompts a
    workflow-folder requirement; tests synthesize the matching layout."""
    prompts_dir = workflow_dir / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for nid in node_ids:
        (prompts_dir / f"{nid}.md").write_text(f"Stub task instruction for {nid}.\n")


def _make_writer_fake(
    payloads_per_node: dict[str, dict[str, dict]],
) -> Callable[[str, Path], subprocess.CompletedProcess[str]]:
    """Build a fake claude_runner that picks payloads based on the node id
    embedded in the prompt header (`# Node: <id>`)."""

    def fake(
        prompt: str, attempt_dir: Path, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        # Find the node id from the first line of the prompt.
        first_line = prompt.splitlines()[0]
        prefix = "# Node: "
        node_id = first_line[len(prefix) :].strip() if first_line.startswith(prefix) else "?"
        job_dir = attempt_dir.parents[3]
        variables_dir = job_dir / "variables"
        variables_dir.mkdir(parents=True, exist_ok=True)
        for var_name, payload in payloads_per_node.get(node_id, {}).items():
            (variables_dir / f"{var_name}.json").write_text(json.dumps(payload))
        (attempt_dir / "stdout.log").write_text(f"(fake) {node_id} succeeded\n")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    return fake


# ---------------------------------------------------------------------------
# submit_job
# ---------------------------------------------------------------------------


def test_submit_creates_job_config_and_seeds_request(tmp_path: Path) -> None:
    yaml_path = _t1_yaml(tmp_path)
    cfg = submit_job(
        workflow_path=yaml_path,
        request_text="Fix the bug",
        job_slug="j1",
        root=tmp_path,
    )
    assert cfg.state == JobState.SUBMITTED
    assert cfg.workflow_name == "t1"

    # Job config persisted.
    on_disk = JobConfig.model_validate_json(paths.job_config_path("j1", root=tmp_path).read_text())
    assert on_disk.job_slug == "j1"

    # Job-request envelope seeded.
    env = Envelope.model_validate_json(
        paths.variable_envelope_path("j1", "request", root=tmp_path).read_text()
    )
    assert env.type == "job-request"
    assert env.value == {"text": "Fix the bug"}


def test_submit_rejects_invalid_workflow(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
workflow: bad
variables:
  x: { type: not-a-real-type }
nodes: []
"""
    )
    from engine.v1.validator import WorkflowValidationError

    with pytest.raises(WorkflowValidationError):
        submit_job(workflow_path=p, request_text="x", job_slug="j", root=tmp_path)


def test_submit_rejects_request_variable_with_wrong_type(tmp_path: Path) -> None:
    p = tmp_path / "wrong.yaml"
    p.write_text(
        """
workflow: w
variables:
  request: { type: bug-report }
nodes: []
"""
    )
    with pytest.raises(JobSubmissionError, match="job-request"):
        submit_job(workflow_path=p, request_text="x", job_slug="j", root=tmp_path)


# ---------------------------------------------------------------------------
# Topological order
# ---------------------------------------------------------------------------


def test_topological_order_respects_after_edges() -> None:
    wf = Workflow(
        workflow="w",
        variables={"x": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="b",
                kind="artifact",
                actor="agent",
                after=["a"],
                inputs={},
                outputs={},
            ),
            ArtifactNode(id="a", kind="artifact", actor="agent", inputs={}, outputs={"o": "$x"}),
        ],
    )
    order = _topological_order(wf)
    assert [n.id for n in order] == ["a", "b"]


def test_topological_order_raises_on_cycle() -> None:
    wf = Workflow(
        workflow="w",
        variables={},
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
    with pytest.raises(DriverError, match="cycle"):
        _topological_order(wf)


# ---------------------------------------------------------------------------
# run_job — happy path
# ---------------------------------------------------------------------------


def test_run_job_drives_t1_to_completed(tmp_path: Path) -> None:
    yaml_path = _t1_yaml(tmp_path)
    submit_job(
        workflow_path=yaml_path,
        request_text="Fix the bug",
        job_slug="j1",
        root=tmp_path,
    )
    fake = _make_writer_fake(
        {
            "write-bug-report": {"bug_report": {"summary": "the bug", "document": "## Bug\n\n."}},
            "write-design-spec": {
                "design_spec": {
                    "title": "Fix",
                    "overview": "Make it return 0.",
                    "document": "## D\n\n.",
                }
            },
        }
    )
    final = run_job(job_slug="j1", root=tmp_path, claude_runner=fake)
    assert final.state == JobState.COMPLETED


def test_run_job_persists_node_runs(tmp_path: Path) -> None:
    yaml_path = _t1_yaml(tmp_path)
    submit_job(
        workflow_path=yaml_path,
        request_text="x",
        job_slug="j1",
        root=tmp_path,
    )
    fake = _make_writer_fake(
        {
            "write-bug-report": {"bug_report": {"summary": "x", "document": "## Bug\n\n."}},
            "write-design-spec": {
                "design_spec": {"title": "t", "overview": "o", "document": "## D\n\n."}
            },
        }
    )
    run_job(job_slug="j1", root=tmp_path, claude_runner=fake)
    for node_id in ("write-bug-report", "write-design-spec"):
        run = NodeRun.model_validate_json(
            paths.node_state_path("j1", node_id, root=tmp_path).read_text()
        )
        assert run.state == NodeRunState.SUCCEEDED
        assert run.attempts == 1


def test_run_job_persists_all_envelopes(tmp_path: Path) -> None:
    yaml_path = _t1_yaml(tmp_path)
    submit_job(
        workflow_path=yaml_path,
        request_text="x",
        job_slug="j1",
        root=tmp_path,
    )
    fake = _make_writer_fake(
        {
            "write-bug-report": {"bug_report": {"summary": "x", "document": "## Bug\n\n."}},
            "write-design-spec": {
                "design_spec": {"title": "t", "overview": "o", "document": "## D\n\n."}
            },
        }
    )
    run_job(job_slug="j1", root=tmp_path, claude_runner=fake)
    for var in ("request", "bug_report", "design_spec"):
        p = paths.variable_envelope_path("j1", var, root=tmp_path)
        assert p.is_file(), f"envelope for {var} missing"
        Envelope.model_validate_json(p.read_text())


# ---------------------------------------------------------------------------
# run_job — failure path
# ---------------------------------------------------------------------------


def test_run_job_marks_failed_when_node_contract_fails(tmp_path: Path) -> None:
    yaml_path = _t1_yaml(tmp_path)
    submit_job(
        workflow_path=yaml_path,
        request_text="x",
        job_slug="j1",
        root=tmp_path,
    )
    # First node writes nothing — bug_report missing.
    fake = _make_writer_fake({})
    final = run_job(job_slug="j1", root=tmp_path, claude_runner=fake)
    assert final.state == JobState.FAILED
    run = NodeRun.model_validate_json(
        paths.node_state_path("j1", "write-bug-report", root=tmp_path).read_text()
    )
    assert run.state == NodeRunState.FAILED
    assert run.last_error is not None


# ---------------------------------------------------------------------------
# run_job — resume after partial completion
# ---------------------------------------------------------------------------


def test_run_job_resumes_skipping_already_succeeded_nodes(tmp_path: Path) -> None:
    yaml_path = _t1_yaml(tmp_path)
    submit_job(
        workflow_path=yaml_path,
        request_text="x",
        job_slug="j1",
        root=tmp_path,
    )

    # First call: only the bug-report node "succeeds" (we control the fake).
    counter = {"calls": 0}

    def fake_first(
        prompt: str, attempt_dir: Path, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        counter["calls"] += 1
        first_line = prompt.splitlines()[0]
        node_id = first_line.removeprefix("# Node: ").strip()
        job_dir = attempt_dir.parents[3]
        variables_dir = job_dir / "variables"
        variables_dir.mkdir(parents=True, exist_ok=True)
        if node_id == "write-bug-report":
            (variables_dir / "bug_report.json").write_text(
                json.dumps({"summary": "x", "document": "## Bug\n\n."})
            )
        # write-design-spec writes nothing → fails
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    final = run_job(job_slug="j1", root=tmp_path, claude_runner=fake_first)
    assert final.state == JobState.FAILED

    # Second call: design-spec node writes the file, bug-report node should
    # NOT be re-dispatched.
    bug_report_calls_first_run = counter["calls"]
    assert bug_report_calls_first_run >= 1

    # Reset the failed node's state to PENDING for the resume sim. (Real
    # resume would happen via operator intervention; for this test we just
    # remove the failed state file so the driver will retry that node.)
    failed_state_path = paths.node_state_path("j1", "write-design-spec", root=tmp_path)
    if failed_state_path.exists():
        failed_state_path.unlink()
    # Reset the job state from FAILED back to RUNNING (the driver's resume
    # path normally takes care of this on real crash; we simulate it here).
    cfg_path = paths.job_config_path("j1", root=tmp_path)
    cfg = JobConfig.model_validate_json(cfg_path.read_text())
    cfg = cfg.model_copy(update={"state": JobState.RUNNING})
    cfg_path.write_text(cfg.model_dump_json(indent=2))

    counter["calls"] = 0

    def fake_second(
        prompt: str, attempt_dir: Path, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        counter["calls"] += 1
        first_line = prompt.splitlines()[0]
        node_id = first_line.removeprefix("# Node: ").strip()
        job_dir = attempt_dir.parents[3]
        variables_dir = job_dir / "variables"
        variables_dir.mkdir(parents=True, exist_ok=True)
        if node_id == "write-design-spec":
            (variables_dir / "design_spec.json").write_text(
                json.dumps({"title": "t", "overview": "o", "document": "## D\n\n."})
            )
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    final = run_job(job_slug="j1", root=tmp_path, claude_runner=fake_second)
    assert final.state == JobState.COMPLETED
    # bug-report node SUCCEEDED in the first run; should NOT have been called again.
    assert counter["calls"] == 1, (
        f"expected only design-spec to be re-dispatched on resume, got {counter['calls']} calls"
    )
