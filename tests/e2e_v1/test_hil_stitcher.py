"""Unit tests for tests/e2e_v1/hil_stitcher.py."""

from __future__ import annotations

import time
from pathlib import Path

from engine.v1.hil import pending_marker_path, write_pending_marker
from shared.v1 import paths
from shared.v1.envelope import Envelope
from shared.v1.workflow import ArtifactNode, VariableSpec, Workflow
from tests.e2e_v1.hil_stitcher import HilStitcher, approve_review_verdict


def _human_review_workflow() -> Workflow:
    return Workflow(
        workflow="t",
        variables={"verdict": VariableSpec(type="review-verdict")},
        nodes=[
            ArtifactNode(
                id="review-x-human",
                kind="artifact",
                actor="human",
                inputs={},
                outputs={"verdict": "$verdict"},
                presentation={"title": "x"},
            )
        ],
    )


def test_stitcher_answers_pending_gate(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    write_pending_marker(
        job_slug="j", node=wf.nodes[0], workflow=wf, root=tmp_path
    )

    stitcher = HilStitcher(
        job_slug="j",
        workflow=wf,
        root=tmp_path,
        policies={"review-x-human": approve_review_verdict},
        poll_interval_seconds=0.05,
    )
    stitcher.start()
    try:
        # Stitcher should pick up the marker, submit, marker should vanish.
        marker = pending_marker_path("j", "review-x-human", root=tmp_path)
        deadline = time.monotonic() + 2.0
        while marker.exists():
            if time.monotonic() >= deadline:
                raise AssertionError("stitcher did not clear the marker")
            time.sleep(0.05)
        # Envelope should be on disk.
        env = Envelope.model_validate_json(
            paths.variable_envelope_path("j", "verdict", root=tmp_path).read_text()
        )
        assert env.value["verdict"] == "approved"
    finally:
        stitcher.stop()
    assert stitcher.errors == []


def test_stitcher_records_error_when_no_policy_registered(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    write_pending_marker(
        job_slug="j", node=wf.nodes[0], workflow=wf, root=tmp_path
    )

    stitcher = HilStitcher(
        job_slug="j",
        workflow=wf,
        root=tmp_path,
        policies={},  # no policies registered
        poll_interval_seconds=0.05,
    )
    stitcher.start()
    try:
        # Wait until error appears.
        deadline = time.monotonic() + 2.0
        while not stitcher.errors:
            if time.monotonic() >= deadline:
                raise AssertionError("stitcher never recorded the missing policy error")
            time.sleep(0.05)
    finally:
        stitcher.stop()
    assert any("no answer policy" in e for e in stitcher.errors)


def test_stitcher_doesnt_double_answer(tmp_path: Path) -> None:
    """After a gate is answered, the stitcher must not retry — submit_hil_answer
    on a removed marker would error otherwise."""
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    write_pending_marker(
        job_slug="j", node=wf.nodes[0], workflow=wf, root=tmp_path
    )
    stitcher = HilStitcher(
        job_slug="j",
        workflow=wf,
        root=tmp_path,
        policies={"review-x-human": approve_review_verdict},
        poll_interval_seconds=0.05,
    )
    stitcher.start()
    try:
        # Wait until the marker is gone, then wait some more to make sure
        # no error is recorded by re-submission attempts.
        marker = pending_marker_path("j", "review-x-human", root=tmp_path)
        deadline = time.monotonic() + 2.0
        while marker.exists():
            if time.monotonic() >= deadline:
                raise AssertionError("stitcher did not clear the marker")
            time.sleep(0.05)
        time.sleep(0.3)
    finally:
        stitcher.stop()
    assert stitcher.errors == []
