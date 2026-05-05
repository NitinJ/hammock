"""Unit tests for engine/v1/hil.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.v1.hil import (
    HilSubmissionError,
    list_pending,
    pending_marker_path,
    remove_pending_marker,
    submit_hil_answer,
    wait_for_node_outputs,
    write_pending_marker,
)
from shared.v1 import paths
from shared.v1.envelope import Envelope
from shared.v1.workflow import ArtifactNode, VariableSpec, Workflow


def _human_review_workflow() -> Workflow:
    """Minimal workflow with a single human-actor node producing a
    review-verdict."""
    return Workflow(
        workflow="t",
        variables={
            "design_spec": VariableSpec(type="design-spec"),
            "verdict": VariableSpec(type="review-verdict"),
        },
        nodes=[
            ArtifactNode(
                id="review-design-spec-human",
                kind="artifact",
                actor="human",
                inputs={"design_spec": "$design_spec"},
                outputs={"verdict": "$verdict"},
                presentation={"title": "Review the design spec"},
            ),
        ],
    )


# ---------------------------------------------------------------------------
# write_pending_marker / list_pending / remove
# ---------------------------------------------------------------------------


def test_write_pending_creates_file(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    write_pending_marker(
        job_slug="j", node=wf.nodes[0], workflow=wf, root=tmp_path
    )
    p = pending_marker_path("j", "review-design-spec-human", root=tmp_path)
    assert p.is_file()


def test_list_pending_reads_from_disk(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    write_pending_marker(
        job_slug="j", node=wf.nodes[0], workflow=wf, root=tmp_path
    )
    items = list_pending("j", root=tmp_path)
    assert len(items) == 1
    item = items[0]
    assert item.node_id == "review-design-spec-human"
    assert item.output_var_names == ["verdict"]
    assert item.presentation == {"title": "Review the design spec"}
    assert item.output_types == {"verdict": "review-verdict"}


def test_list_pending_returns_empty_when_no_dir(tmp_path: Path) -> None:
    assert list_pending("j", root=tmp_path) == []


def test_remove_pending_marker_idempotent(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    write_pending_marker(
        job_slug="j", node=wf.nodes[0], workflow=wf, root=tmp_path
    )
    remove_pending_marker("j", "review-design-spec-human", root=tmp_path)
    remove_pending_marker("j", "review-design-spec-human", root=tmp_path)  # again
    assert not pending_marker_path("j", "review-design-spec-human", root=tmp_path).exists()


# ---------------------------------------------------------------------------
# submit_hil_answer — happy path
# ---------------------------------------------------------------------------


def test_submit_writes_envelope_and_clears_marker(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    write_pending_marker(
        job_slug="j", node=wf.nodes[0], workflow=wf, root=tmp_path
    )
    submit_hil_answer(
        job_slug="j",
        node_id="review-design-spec-human",
        var_name="verdict",
        value_payload={
            "verdict": "approved",
            "summary": "looks good",
            "unresolved_concerns": [],
            "addressed_in_this_iteration": [],
        },
        root=tmp_path,
        workflow=wf,
    )
    env_path = paths.variable_envelope_path("j", "verdict", root=tmp_path)
    assert env_path.is_file()
    env = Envelope.model_validate_json(env_path.read_text())
    assert env.type == "review-verdict"
    assert env.value["verdict"] == "approved"
    # Marker should be gone now that the only required output is in.
    assert not pending_marker_path("j", "review-design-spec-human", root=tmp_path).exists()


# ---------------------------------------------------------------------------
# submit_hil_answer — rejection paths
# ---------------------------------------------------------------------------


def test_submit_unknown_node_raises(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    with pytest.raises(HilSubmissionError, match="unknown or non-artifact"):
        submit_hil_answer(
            job_slug="j",
            node_id="nope",
            var_name="verdict",
            value_payload={},
            root=tmp_path,
            workflow=wf,
        )


def test_submit_to_agent_node_raises(tmp_path: Path) -> None:
    """HIL submission only applies to human-actor nodes."""
    paths.ensure_job_layout("j", root=tmp_path)
    wf = Workflow(
        workflow="t",
        variables={"verdict": VariableSpec(type="review-verdict")},
        nodes=[
            ArtifactNode(
                id="agent-node",
                kind="artifact",
                actor="agent",
                inputs={},
                outputs={"verdict": "$verdict"},
            ),
        ],
    )
    with pytest.raises(HilSubmissionError, match="not 'human'"):
        submit_hil_answer(
            job_slug="j",
            node_id="agent-node",
            var_name="verdict",
            value_payload={
                "verdict": "approved",
                "summary": "x",
                "unresolved_concerns": [],
                "addressed_in_this_iteration": [],
            },
            root=tmp_path,
            workflow=wf,
        )


def test_submit_unknown_var_raises(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    with pytest.raises(HilSubmissionError, match="does not declare output"):
        submit_hil_answer(
            job_slug="j",
            node_id="review-design-spec-human",
            var_name="not_an_output",
            value_payload={},
            root=tmp_path,
            workflow=wf,
        )


def test_submit_invalid_payload_rejected_and_no_envelope_left(tmp_path: Path) -> None:
    """Verification failure must not leave a half-written envelope on disk."""
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    write_pending_marker(
        job_slug="j", node=wf.nodes[0], workflow=wf, root=tmp_path
    )
    with pytest.raises(HilSubmissionError, match="rejected"):
        submit_hil_answer(
            job_slug="j",
            node_id="review-design-spec-human",
            var_name="verdict",
            value_payload={"verdict": "approved", "summary": ""},  # empty summary
            root=tmp_path,
            workflow=wf,
        )
    # The envelope must NOT be on disk.
    env_path = paths.variable_envelope_path("j", "verdict", root=tmp_path)
    assert not env_path.is_file()
    # The pending marker stays so the human can retry.
    assert pending_marker_path("j", "review-design-spec-human", root=tmp_path).is_file()


# ---------------------------------------------------------------------------
# wait_for_node_outputs
# ---------------------------------------------------------------------------


def test_wait_returns_true_when_marker_already_gone(tmp_path: Path) -> None:
    """Default poll interval is 1s but marker absence is detected on
    first iteration so this should return immediately."""
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    # No marker written → wait returns True immediately.
    ok = wait_for_node_outputs(
        node=wf.nodes[0],
        workflow=wf,
        job_slug="j",
        root=tmp_path,
        poll_interval_seconds=0.01,
        timeout_seconds=1.0,
    )
    assert ok is True


def test_wait_returns_false_on_timeout(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    write_pending_marker(
        job_slug="j", node=wf.nodes[0], workflow=wf, root=tmp_path
    )
    ok = wait_for_node_outputs(
        node=wf.nodes[0],
        workflow=wf,
        job_slug="j",
        root=tmp_path,
        poll_interval_seconds=0.05,
        timeout_seconds=0.2,
    )
    assert ok is False


def test_wait_returns_true_after_concurrent_submission(tmp_path: Path) -> None:
    """Spawn a thread that submits after a short delay; the wait should
    pick it up and return True."""
    import threading
    import time as _time

    paths.ensure_job_layout("j", root=tmp_path)
    wf = _human_review_workflow()
    write_pending_marker(
        job_slug="j", node=wf.nodes[0], workflow=wf, root=tmp_path
    )

    def submit_after_delay() -> None:
        _time.sleep(0.1)
        submit_hil_answer(
            job_slug="j",
            node_id="review-design-spec-human",
            var_name="verdict",
            value_payload={
                "verdict": "approved",
                "summary": "ok",
                "unresolved_concerns": [],
                "addressed_in_this_iteration": [],
            },
            root=tmp_path,
            workflow=wf,
        )

    submitter = threading.Thread(target=submit_after_delay)
    submitter.start()
    try:
        ok = wait_for_node_outputs(
            node=wf.nodes[0],
            workflow=wf,
            job_slug="j",
            root=tmp_path,
            poll_interval_seconds=0.05,
            timeout_seconds=2.0,
        )
        assert ok is True
    finally:
        submitter.join(timeout=2.0)
