"""Driver tests covering HIL flow (T2 capability)."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from engine.v1.driver import run_job, submit_job
from engine.v1.hil import (
    pending_marker_path,
    submit_hil_answer,
)
from engine.v1.loader import load_workflow
from shared.v1 import paths
from shared.v1.envelope import Envelope
from shared.v1.job import JobConfig, JobState, NodeRun, NodeRunState


def _t2_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "t2.yaml"
    p.write_text(
        """
schema_version: 1
workflow: t2
variables:
  request: { type: job-request }
  bug_report: { type: bug-report }
  design_spec: { type: design-spec }
  design_spec_review_agent: { type: review-verdict }
  design_spec_review_human: { type: review-verdict }
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
  - id: review-design-spec-agent
    kind: artifact
    actor: agent
    after: [write-design-spec]
    inputs: { design_spec: $design_spec }
    outputs: { verdict: $design_spec_review_agent }
  - id: review-design-spec-human
    kind: artifact
    actor: human
    after: [review-design-spec-agent]
    inputs:
      design_spec: $design_spec
      agent_verdict: $design_spec_review_agent
    outputs: { verdict: $design_spec_review_human }
    presentation:
      title: "Review the design spec"
"""
    )
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for nid in ("write-bug-report", "write-design-spec", "review-design-spec-agent"):
        (prompts_dir / f"{nid}.md").write_text(f"Stub task for {nid}.\n")
    return p


def _make_writer_fake(
    payloads_per_node: dict[str, dict[str, dict]],
) -> Callable[[str, Path], subprocess.CompletedProcess[str]]:
    def fake(
        prompt: str, attempt_dir: Path, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        node_id = prompt.splitlines()[0].removeprefix("# Node: ").strip()
        job_dir = attempt_dir.parents[3]
        variables_dir = job_dir / "variables"
        variables_dir.mkdir(parents=True, exist_ok=True)
        for var_name, payload in payloads_per_node.get(node_id, {}).items():
            (variables_dir / f"{var_name}.json").write_text(json.dumps(payload))
        (attempt_dir / "stdout.log").write_text(f"(fake) {node_id} done\n")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    return fake


def _agent_payloads_for_t2() -> dict[str, dict[str, dict]]:
    return {
        "write-bug-report": {"bug_report": {"summary": "the bug", "document": "## Bug\n\n."}},
        "write-design-spec": {
            "design_spec": {
                "title": "Fix it",
                "overview": "Plan.",
                "document": "## D\n\n.",
            }
        },
        "review-design-spec-agent": {
            "design_spec_review_agent": {
                "verdict": "approved",
                "summary": "agent approves",
            }
        },
    }


# ---------------------------------------------------------------------------
# Happy path: human submits via API, driver wakes, job COMPLETED
# ---------------------------------------------------------------------------


def test_driver_waits_for_hil_then_completes(tmp_path: Path) -> None:
    yaml_path = _t2_yaml(tmp_path)
    job_slug = "j1"
    submit_job(
        workflow_path=yaml_path,
        request_text="Fix the bug",
        job_slug=job_slug,
        root=tmp_path,
    )
    workflow = load_workflow(yaml_path)

    # Run the driver in a background thread; submit the human answer
    # after a short delay from the main thread.
    fake = _make_writer_fake(_agent_payloads_for_t2())

    final_cfg: dict[str, JobConfig] = {}

    def drive() -> None:
        final_cfg["x"] = run_job(
            job_slug=job_slug,
            root=tmp_path,
            claude_runner=fake,
            hil_poll_interval_seconds=0.05,
            hil_timeout_seconds=10.0,
        )

    driver_thread = threading.Thread(target=drive, daemon=True)
    driver_thread.start()

    # Wait until the pending marker appears (driver hit the HIL gate).
    marker = pending_marker_path(job_slug, "review-design-spec-human", root=tmp_path)
    deadline = time.monotonic() + 10.0
    while not marker.exists():
        if time.monotonic() >= deadline:
            raise AssertionError("driver never wrote pending marker")
        time.sleep(0.05)

    # Submit the human answer.
    submit_hil_answer(
        job_slug=job_slug,
        node_id="review-design-spec-human",
        var_name="design_spec_review_human",
        value_payload={
            "verdict": "approved",
            "summary": "looks good",
        },
        root=tmp_path,
        workflow=workflow,
    )

    driver_thread.join(timeout=10.0)
    assert not driver_thread.is_alive(), "driver did not return after submission"
    assert final_cfg["x"].state == JobState.COMPLETED


def test_driver_persists_human_node_run_succeeded(tmp_path: Path) -> None:
    yaml_path = _t2_yaml(tmp_path)
    job_slug = "j1"
    submit_job(
        workflow_path=yaml_path,
        request_text="Fix",
        job_slug=job_slug,
        root=tmp_path,
    )
    workflow = load_workflow(yaml_path)
    fake = _make_writer_fake(_agent_payloads_for_t2())

    def drive() -> None:
        run_job(
            job_slug=job_slug,
            root=tmp_path,
            claude_runner=fake,
            hil_poll_interval_seconds=0.05,
            hil_timeout_seconds=10.0,
        )

    driver_thread = threading.Thread(target=drive, daemon=True)
    driver_thread.start()

    marker = pending_marker_path(job_slug, "review-design-spec-human", root=tmp_path)
    deadline = time.monotonic() + 10.0
    while not marker.exists():
        if time.monotonic() >= deadline:
            raise AssertionError("driver never wrote pending marker")
        time.sleep(0.05)

    submit_hil_answer(
        job_slug=job_slug,
        node_id="review-design-spec-human",
        var_name="design_spec_review_human",
        value_payload={
            "verdict": "approved",
            "summary": "ok",
        },
        root=tmp_path,
        workflow=workflow,
    )
    driver_thread.join(timeout=10.0)

    # Human node state should be SUCCEEDED.
    run = NodeRun.model_validate_json(
        paths.node_state_path(job_slug, "review-design-spec-human", root=tmp_path).read_text()
    )
    assert run.state == NodeRunState.SUCCEEDED


# ---------------------------------------------------------------------------
# Timeout path: no submission → driver fails the job
# ---------------------------------------------------------------------------


def test_driver_fails_on_hil_timeout(tmp_path: Path) -> None:
    yaml_path = _t2_yaml(tmp_path)
    job_slug = "j1"
    submit_job(
        workflow_path=yaml_path,
        request_text="x",
        job_slug=job_slug,
        root=tmp_path,
    )
    fake = _make_writer_fake(_agent_payloads_for_t2())
    final = run_job(
        job_slug=job_slug,
        root=tmp_path,
        claude_runner=fake,
        hil_poll_interval_seconds=0.02,
        hil_timeout_seconds=0.2,  # short — no submission will arrive
    )
    assert final.state == JobState.FAILED
    run = NodeRun.model_validate_json(
        paths.node_state_path(job_slug, "review-design-spec-human", root=tmp_path).read_text()
    )
    assert run.state == NodeRunState.FAILED
    assert run.last_error is not None
    assert "timed out" in run.last_error


# ---------------------------------------------------------------------------
# State transitions: driver hits BLOCKED_ON_HUMAN, then back to RUNNING,
# then COMPLETED
# ---------------------------------------------------------------------------


def test_driver_transitions_through_blocked_on_human(tmp_path: Path) -> None:
    yaml_path = _t2_yaml(tmp_path)
    job_slug = "j1"
    submit_job(
        workflow_path=yaml_path,
        request_text="x",
        job_slug=job_slug,
        root=tmp_path,
    )
    workflow = load_workflow(yaml_path)
    fake = _make_writer_fake(_agent_payloads_for_t2())

    def drive() -> None:
        run_job(
            job_slug=job_slug,
            root=tmp_path,
            claude_runner=fake,
            hil_poll_interval_seconds=0.02,
            hil_timeout_seconds=10.0,
        )

    driver_thread = threading.Thread(target=drive, daemon=True)
    driver_thread.start()

    marker = pending_marker_path(job_slug, "review-design-spec-human", root=tmp_path)
    deadline = time.monotonic() + 10.0
    while not marker.exists():
        if time.monotonic() >= deadline:
            raise AssertionError("never blocked on human")
        time.sleep(0.02)

    # At this moment the job state on disk should read BLOCKED_ON_HUMAN.
    cfg = JobConfig.model_validate_json(paths.job_config_path(job_slug, root=tmp_path).read_text())
    assert cfg.state == JobState.BLOCKED_ON_HUMAN

    submit_hil_answer(
        job_slug=job_slug,
        node_id="review-design-spec-human",
        var_name="design_spec_review_human",
        value_payload={
            "verdict": "approved",
            "summary": "ok",
        },
        root=tmp_path,
        workflow=workflow,
    )
    driver_thread.join(timeout=10.0)

    cfg_final = JobConfig.model_validate_json(
        paths.job_config_path(job_slug, root=tmp_path).read_text()
    )
    assert cfg_final.state == JobState.COMPLETED


# ---------------------------------------------------------------------------
# Envelope persisted by submission API is what downstream sees
# ---------------------------------------------------------------------------


def test_human_envelope_persisted_after_submission(tmp_path: Path) -> None:
    yaml_path = _t2_yaml(tmp_path)
    job_slug = "j1"
    submit_job(
        workflow_path=yaml_path,
        request_text="x",
        job_slug=job_slug,
        root=tmp_path,
    )
    workflow = load_workflow(yaml_path)
    fake = _make_writer_fake(_agent_payloads_for_t2())

    def drive() -> None:
        run_job(
            job_slug=job_slug,
            root=tmp_path,
            claude_runner=fake,
            hil_poll_interval_seconds=0.02,
            hil_timeout_seconds=10.0,
        )

    driver_thread = threading.Thread(target=drive, daemon=True)
    driver_thread.start()

    marker = pending_marker_path(job_slug, "review-design-spec-human", root=tmp_path)
    deadline = time.monotonic() + 10.0
    while not marker.exists():
        if time.monotonic() >= deadline:
            raise AssertionError("never blocked on human")
        time.sleep(0.02)

    submit_hil_answer(
        job_slug=job_slug,
        node_id="review-design-spec-human",
        var_name="design_spec_review_human",
        value_payload={
            "verdict": "approved",
            "summary": "human approves",
        },
        root=tmp_path,
        workflow=workflow,
    )
    driver_thread.join(timeout=10.0)

    env = Envelope.model_validate_json(
        paths.variable_envelope_path(
            job_slug, "design_spec_review_human", root=tmp_path
        ).read_text()
    )
    assert env.type == "review-verdict"
    assert env.value["verdict"] == "approved"
    assert env.value["summary"] == "human approves"
    assert env.producer_node == "review-design-spec-human"
