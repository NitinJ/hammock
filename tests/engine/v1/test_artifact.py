"""Unit tests for engine/v1/artifact.py.

We inject a fake `claude_runner` so unit tests don't need the real Claude
binary or network. The fake's job: pretend the agent ran by writing the
expected output JSON file(s) to the job dir before "exiting".
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from engine.v1.artifact import dispatch_artifact_agent
from shared.v1 import paths
from shared.v1.envelope import Envelope, make_envelope
from shared.v1.workflow import ArtifactNode, VariableSpec, Workflow


def _seed_request(*, root: Path, job_slug: str, text: str = "Fix the bug") -> None:
    """Seed the job-request variable on disk (engine writes it at submit
    time in production)."""
    paths.ensure_job_layout(job_slug, root=root)
    env = make_envelope(
        type_name="job-request",
        producer_node="<engine>",
        value_payload={"text": text},
    )
    paths.variable_envelope_path(job_slug, "request", root=root).write_text(
        env.model_dump_json()
    )


def _make_writer_fake(
    payloads: dict[str, dict],
) -> Callable[[str, Path], subprocess.CompletedProcess[str]]:
    """Build a fake claude_runner that writes JSON payloads keyed by
    variable name into the job's variables/ dir, mimicking what a real
    agent would do for an artifact node."""

    def fake(prompt: str, attempt_dir: Path) -> subprocess.CompletedProcess[str]:
        # job_dir = attempt_dir.parent.parent.parent.parent
        # path is jobs/<slug>/nodes/<id>/runs/<n>
        job_dir = attempt_dir.parents[3]
        variables_dir = job_dir / "variables"
        variables_dir.mkdir(parents=True, exist_ok=True)
        for var_name, payload in payloads.items():
            (variables_dir / f"{var_name}.json").write_text(json.dumps(payload))
        # Touch stdout/stderr so the dispatcher can verify they exist.
        (attempt_dir / "stdout.log").write_text("(fake) agent succeeded\n")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(
            args=["claude", "-p", "<prompt>"], returncode=0, stdout=b"", stderr=b""
        )

    return fake


def _make_failing_fake() -> Callable[[str, Path], subprocess.CompletedProcess[str]]:
    def fake(prompt: str, attempt_dir: Path) -> subprocess.CompletedProcess[str]:
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("(fake) agent crashed\n")
        return subprocess.CompletedProcess(
            args=["claude"], returncode=2, stdout=b"", stderr=b""
        )

    return fake


def _t1_workflow() -> Workflow:
    return Workflow(
        workflow="t",
        variables={
            "request": VariableSpec(type="job-request"),
            "bug_report": VariableSpec(type="bug-report"),
        },
        nodes=[
            ArtifactNode(
                id="write-bug-report",
                kind="artifact",
                actor="agent",
                inputs={"request": "$request"},
                outputs={"bug_report": "$bug_report"},
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_dispatch_writes_prompt_to_attempt_dir(tmp_path: Path) -> None:
    job_slug = "j1"
    _seed_request(root=tmp_path, job_slug=job_slug)
    wf = _t1_workflow()
    fake = _make_writer_fake({"bug_report": {"summary": "the bug"}})
    result = dispatch_artifact_agent(
        node=wf.nodes[0],
        workflow=wf,
        job_slug=job_slug,
        root=tmp_path,
        claude_runner=fake,
    )
    assert result.succeeded
    prompt_path = result.attempt_dir / "prompt.md"
    assert prompt_path.is_file()
    assert "write-bug-report" in prompt_path.read_text()


def test_dispatch_persists_envelope(tmp_path: Path) -> None:
    job_slug = "j1"
    _seed_request(root=tmp_path, job_slug=job_slug)
    wf = _t1_workflow()
    fake = _make_writer_fake({"bug_report": {"summary": "the bug"}})
    result = dispatch_artifact_agent(
        node=wf.nodes[0],
        workflow=wf,
        job_slug=job_slug,
        root=tmp_path,
        claude_runner=fake,
    )
    assert result.succeeded
    env_path = paths.variable_envelope_path(job_slug, "bug_report", root=tmp_path)
    assert env_path.is_file()
    env = Envelope.model_validate_json(env_path.read_text())
    assert env.type == "bug-report"
    assert env.producer_node == "write-bug-report"
    assert env.value == {
        "summary": "the bug",
        "repro_steps": [],
        "expected_behaviour": None,
        "actual_behaviour": None,
    }


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_dispatch_fails_when_subprocess_nonzero(tmp_path: Path) -> None:
    job_slug = "j1"
    _seed_request(root=tmp_path, job_slug=job_slug)
    wf = _t1_workflow()
    result = dispatch_artifact_agent(
        node=wf.nodes[0],
        workflow=wf,
        job_slug=job_slug,
        root=tmp_path,
        claude_runner=_make_failing_fake(),
    )
    assert not result.succeeded
    assert result.error is not None
    assert "rc=2" in result.error


def test_dispatch_fails_when_required_output_missing(tmp_path: Path) -> None:
    """Agent succeeds (rc=0) but doesn't write the required output file."""
    job_slug = "j1"
    _seed_request(root=tmp_path, job_slug=job_slug)
    wf = _t1_workflow()
    fake = _make_writer_fake({})  # writes nothing
    result = dispatch_artifact_agent(
        node=wf.nodes[0],
        workflow=wf,
        job_slug=job_slug,
        root=tmp_path,
        claude_runner=fake,
    )
    assert not result.succeeded
    assert result.error is not None
    assert "not produced" in result.error


def test_dispatch_fails_on_invalid_json_output(tmp_path: Path) -> None:
    job_slug = "j1"
    _seed_request(root=tmp_path, job_slug=job_slug)
    wf = _t1_workflow()

    def broken(prompt: str, attempt_dir: Path) -> subprocess.CompletedProcess[str]:
        job_dir = attempt_dir.parents[3]
        (job_dir / "variables").mkdir(parents=True, exist_ok=True)
        (job_dir / "variables" / "bug_report.json").write_text("{ broken")
        (attempt_dir / "stdout.log").write_text("ok\n")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    result = dispatch_artifact_agent(
        node=wf.nodes[0],
        workflow=wf,
        job_slug=job_slug,
        root=tmp_path,
        claude_runner=broken,
    )
    assert not result.succeeded
    assert "not valid JSON" in (result.error or "")


# ---------------------------------------------------------------------------
# Optional outputs — agent didn't write the file but no failure
# ---------------------------------------------------------------------------


def test_optional_output_skipped_when_not_produced(tmp_path: Path) -> None:
    job_slug = "j1"
    _seed_request(root=tmp_path, job_slug=job_slug)
    wf = Workflow(
        workflow="t",
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
                outputs={"bug_report?": "$bug_report"},  # optional
            ),
        ],
    )
    fake = _make_writer_fake({})  # writes nothing
    result = dispatch_artifact_agent(
        node=wf.nodes[0],
        workflow=wf,
        job_slug=job_slug,
        root=tmp_path,
        claude_runner=fake,
    )
    assert result.succeeded
    env_path = paths.variable_envelope_path(job_slug, "bug_report", root=tmp_path)
    assert not env_path.is_file()


# ---------------------------------------------------------------------------
# Attempt directory layout
# ---------------------------------------------------------------------------


def test_attempt_dir_layout_default_attempt_1(tmp_path: Path) -> None:
    job_slug = "j1"
    _seed_request(root=tmp_path, job_slug=job_slug)
    wf = _t1_workflow()
    fake = _make_writer_fake({"bug_report": {"summary": "x"}})
    result = dispatch_artifact_agent(
        node=wf.nodes[0],
        workflow=wf,
        job_slug=job_slug,
        root=tmp_path,
        claude_runner=fake,
    )
    expected = paths.node_attempt_dir(job_slug, "write-bug-report", 1, root=tmp_path)
    assert result.attempt_dir == expected
    assert (expected / "prompt.md").is_file()
    assert (expected / "stdout.log").is_file()
