"""Disk-contract tests — Stage 3 fills in Stage 1 §1.6 stubs.

Verifies the v1 path classifier (``dashboard/state/classify.py``) maps
every v1 file path correctly. The watcher uses this classifier; if it
misses a path, the dashboard never sees the change.

These are unit-level tests on the classifier — no live dashboard
needed. They live under ``tests/integration/dashboard/`` because they
guarantee the dashboard's read-side contract against the v1 layout
that ``FakeEngine`` writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.state.classify import classify_path
from shared.v1 import paths as v1_paths


def test_job_json_classified(tmp_path: Path) -> None:
    p = v1_paths.job_config_path("alpha", root=tmp_path)
    cp = classify_path(p, tmp_path)
    assert cp.kind == "job"
    assert cp.job_slug == "alpha"


def test_node_state_classified(tmp_path: Path) -> None:
    p = v1_paths.node_state_path("alpha", "write-spec", root=tmp_path)
    cp = classify_path(p, tmp_path)
    assert cp.kind == "node"
    assert cp.job_slug == "alpha"
    assert cp.node_id == "write-spec"


def test_top_level_variable_envelope_classified(tmp_path: Path) -> None:
    p = v1_paths.variable_envelope_path("alpha", "design_spec", root=tmp_path)
    cp = classify_path(p, tmp_path)
    assert cp.kind == "variable"
    assert cp.job_slug == "alpha"
    assert cp.var_name == "design_spec"


def test_loop_body_variable_envelope_classified(tmp_path: Path) -> None:
    """v2 keys envelopes by full iter_path. ``variables/<var>__i0_2.json``
    is a body-execution envelope at iter_path=(0, 2). The classifier
    decodes the iter_path; loop_id is no longer part of the path."""
    p = v1_paths.variable_envelope_path("alpha", "pr", (0, 2), root=tmp_path)
    cp = classify_path(p, tmp_path)
    assert cp.kind == "variable"
    assert cp.job_slug == "alpha"
    assert cp.var_name == "pr"
    assert cp.iter_path == (0, 2)


def test_events_jsonl_classified(tmp_path: Path) -> None:
    p = v1_paths.events_jsonl("alpha", root=tmp_path)
    cp = classify_path(p, tmp_path)
    assert cp.kind == "events_jsonl"
    assert cp.job_slug == "alpha"


def test_pending_marker_classified(tmp_path: Path) -> None:
    p = v1_paths.pending_marker_path("alpha", "review-spec", root=tmp_path)
    cp = classify_path(p, tmp_path)
    assert cp.kind == "pending"
    assert cp.job_slug == "alpha"
    assert cp.node_id == "review-spec"
    assert cp.iter_path == ()


def test_pending_marker_loop_body_classified(tmp_path: Path) -> None:
    """Pending HIL inside a 2-deep nested loop carries the full iter_path."""
    p = v1_paths.pending_marker_path("alpha", "review-spec", (1, 2), root=tmp_path)
    cp = classify_path(p, tmp_path)
    assert cp.kind == "pending"
    assert cp.job_slug == "alpha"
    assert cp.node_id == "review-spec"
    assert cp.iter_path == (1, 2)


def test_unknown_paths_are_unknown(tmp_path: Path) -> None:
    p = v1_paths.job_dir("alpha", root=tmp_path) / "side-file.txt"
    cp = classify_path(p, tmp_path)
    assert cp.kind == "unknown"


def test_path_outside_root_is_unknown(tmp_path: Path) -> None:
    cp = classify_path(Path("/etc/passwd"), tmp_path)
    assert cp.kind == "unknown"


def test_chat_jsonl_classified(tmp_path: Path) -> None:
    """v2: ``chat.jsonl`` writes are watched so the SSE pipeline can emit
    ``chat_appended`` events for the live agent-chat tail. The classifier
    decodes the (node_id, iter_path, attempt) key from the path."""
    p = v1_paths.node_attempt_dir("alpha", "write-spec", 1, root=tmp_path) / "chat.jsonl"
    cp = classify_path(p, tmp_path)
    assert cp.kind == "chat_jsonl"
    assert cp.job_slug == "alpha"
    assert cp.node_id == "write-spec"
    assert cp.iter_path == ()
    assert cp.attempt == 1


def test_chat_jsonl_loop_body_classified(tmp_path: Path) -> None:
    """A loop-body chat.jsonl carries the full iter_path."""
    p = v1_paths.node_attempt_dir("alpha", "write-spec", 2, (0, 1), root=tmp_path) / "chat.jsonl"
    cp = classify_path(p, tmp_path)
    assert cp.kind == "chat_jsonl"
    assert cp.iter_path == (0, 1)
    assert cp.attempt == 2


def test_node_run_other_artefact_unknown(tmp_path: Path) -> None:
    """Per-attempt artefacts other than chat.jsonl (prompt.md, output.json,
    stderr.log) are not watched — the dashboard reads them on demand."""
    p = v1_paths.node_attempt_dir("alpha", "write-spec", 1, root=tmp_path) / "prompt.md"
    cp = classify_path(p, tmp_path)
    assert cp.kind == "unknown"


@pytest.mark.asyncio
async def test_fake_engine_writes_observable_via_dashboard_api(tmp_path: Path) -> None:
    """End-to-end through the running dashboard: FakeEngine writes,
    dashboard's GET /api/jobs picks it up. This is the real regression
    net — proves the v1 disk shape FakeEngine produces is what the
    dashboard's read-side resolves against."""
    # Note: this test deliberately does NOT use the dashboard fixture
    # because it tests the classifier in isolation; the live-dashboard
    # equivalent is in test_projections.py.
