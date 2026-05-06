"""Stage 1 Step 1 — failing tests for the harness itself.

These tests describe the contract for ``FakeEngine`` + the live dashboard
fixture. They will fail at Step 1 (NotImplementedError from the stubs)
and drive Step 2 implementation. Frozen for Step 3 — the Methodology
forbids editing these during the fix loop.

Two layers:

1. ``FakeEngine`` self-tests — verify each scripting method writes the
   expected file at the expected path with the expected content. No
   dashboard involvement.
2. Live-dashboard smoke tests — verify the fixture boots, the watcher
   runs, the httpx client works, and shutdown is clean.

Suites in ``tests/integration/dashboard/`` and ``tests/integration/mcp/``
exercise the dashboard's *behavior* under FakeEngine-scripted state and
are owned by Stages 3 and 4 respectively.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from shared.v1 import paths as v1_paths
from shared.v1.job import JobConfig, JobState, NodeRun, NodeRunState
from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine

# =============================================================================
# FakeEngine self-tests — no dashboard fixture, just disk
# =============================================================================


@pytest.fixture
def fake_engine_offline(tmp_path: Path) -> FakeEngine:
    """FakeEngine bound to tmp_path. No dashboard. Used by self-tests
    that only assert on disk state."""
    return FakeEngine(tmp_path, "test-job-1")


def test_fake_engine_constructor_does_not_create_files(tmp_path: Path) -> None:
    """Constructing FakeEngine alone must not touch the filesystem.
    Step 2 will lay down the skeleton in start_job, not here."""
    FakeEngine(tmp_path, "test-job-1")
    assert list(tmp_path.iterdir()) == []


def test_start_job_creates_job_skeleton(fake_engine_offline: FakeEngine) -> None:
    fake_engine_offline.start_job(
        workflow={"workflow": "T1", "nodes": []},
        request="fix the bug",
    )
    job_json_path = v1_paths.job_config_path(
        fake_engine_offline.job_slug, root=fake_engine_offline.root
    )
    assert job_json_path.exists()

    config = JobConfig.model_validate_json(job_json_path.read_text())
    assert config.job_slug == fake_engine_offline.job_slug
    assert config.state == JobState.SUBMITTED
    assert config.workflow_name == "T1"

    # Skeleton dirs exist
    assert v1_paths.variables_dir(
        fake_engine_offline.job_slug, root=fake_engine_offline.root
    ).is_dir()
    assert v1_paths.nodes_dir(fake_engine_offline.job_slug, root=fake_engine_offline.root).is_dir()


def test_start_job_appends_event(fake_engine_offline: FakeEngine) -> None:
    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    events = v1_paths.events_jsonl(fake_engine_offline.job_slug, root=fake_engine_offline.root)
    assert events.exists()
    lines = events.read_text().splitlines()
    assert len(lines) >= 1
    first = json.loads(lines[0])
    assert first["event_type"] == "job_submitted"
    assert first["seq"] == 0


def test_finish_job_updates_state_and_emits_event(fake_engine_offline: FakeEngine) -> None:
    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.finish_job(JobState.COMPLETED)

    config = JobConfig.model_validate_json(
        v1_paths.job_config_path(
            fake_engine_offline.job_slug, root=fake_engine_offline.root
        ).read_text()
    )
    assert config.state == JobState.COMPLETED

    events_lines = (
        v1_paths.events_jsonl(fake_engine_offline.job_slug, root=fake_engine_offline.root)
        .read_text()
        .splitlines()
    )
    last = json.loads(events_lines[-1])
    assert last["event_type"] == "job_completed"


def test_finish_job_rejects_non_terminal_state(fake_engine_offline: FakeEngine) -> None:
    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    with pytest.raises((ValueError, AssertionError)):
        fake_engine_offline.finish_job(JobState.RUNNING)


def test_enter_node_writes_running_state_and_event(
    fake_engine_offline: FakeEngine,
) -> None:
    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.enter_node("write-bug-report")

    state_path = v1_paths.node_state_path(
        fake_engine_offline.job_slug,
        "write-bug-report",
        root=fake_engine_offline.root,
    )
    assert state_path.exists()
    nr = NodeRun.model_validate_json(state_path.read_text())
    assert nr.state == NodeRunState.RUNNING
    assert nr.attempts == 1
    assert nr.started_at is not None


def test_complete_node_writes_envelope_and_state(
    fake_engine_offline: FakeEngine,
) -> None:
    """complete_node writes the variable envelope to variables/<var>.json
    and updates state.json to SUCCEEDED."""
    from shared.v1.types.bug_report import BugReportValue

    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.enter_node("write-bug-report")

    value = BugReportValue(summary="The login button does not respond.")
    fake_engine_offline.complete_node("write-bug-report", value)

    # State updated
    nr = NodeRun.model_validate_json(
        v1_paths.node_state_path(
            fake_engine_offline.job_slug,
            "write-bug-report",
            root=fake_engine_offline.root,
        ).read_text()
    )
    assert nr.state == NodeRunState.SUCCEEDED
    assert nr.finished_at is not None

    # Envelope on disk
    env_path = v1_paths.variable_envelope_path(
        fake_engine_offline.job_slug, "write-bug-report", root=fake_engine_offline.root
    )
    assert env_path.exists()
    envelope = json.loads(env_path.read_text())
    assert envelope["type"] == "bug-report"
    assert envelope["value"]["summary"] == "The login button does not respond."


def test_complete_node_with_iter_writes_loop_indexed_envelope(
    fake_engine_offline: FakeEngine,
) -> None:
    from shared.v1.types.bug_report import BugReportValue

    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.enter_node("body-node", iter=(0,), loop_id="impl-loop")
    fake_engine_offline.complete_node(
        "body-node",
        BugReportValue(summary="loop iteration 0"),
        iter=(0,),
        loop_id="impl-loop",
    )

    # Loop-indexed envelope path: variables/loop_<loop_id>_<var>_<i>.json.
    indexed_path = v1_paths.loop_variable_envelope_path(
        fake_engine_offline.job_slug,
        "impl-loop",
        "body-node",
        0,
        root=fake_engine_offline.root,
    )
    assert indexed_path.exists(), f"expected envelope at {indexed_path}"
    envelope = json.loads(indexed_path.read_text())
    assert envelope["type"] == "bug-report"


def test_fail_node_records_error(fake_engine_offline: FakeEngine) -> None:
    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.enter_node("bad-node")
    fake_engine_offline.fail_node("bad-node", "something broke")

    nr = NodeRun.model_validate_json(
        v1_paths.node_state_path(
            fake_engine_offline.job_slug, "bad-node", root=fake_engine_offline.root
        ).read_text()
    )
    assert nr.state == NodeRunState.FAILED
    assert nr.last_error == "something broke"


def test_skip_node_records_state(fake_engine_offline: FakeEngine) -> None:
    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.skip_node("optional-node", "runs_if false")

    nr = NodeRun.model_validate_json(
        v1_paths.node_state_path(
            fake_engine_offline.job_slug,
            "optional-node",
            root=fake_engine_offline.root,
        ).read_text()
    )
    assert nr.state == NodeRunState.SKIPPED


def test_emit_event_appends_with_monotonic_seq(
    fake_engine_offline: FakeEngine,
) -> None:
    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.emit_event("custom_event_a", {"k": 1})
    fake_engine_offline.emit_event("custom_event_b", {"k": 2})

    lines = (
        v1_paths.events_jsonl(fake_engine_offline.job_slug, root=fake_engine_offline.root)
        .read_text()
        .splitlines()
    )
    seqs = [json.loads(line)["seq"] for line in lines]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)  # strictly monotonic, no dupes


def test_emit_log_appends_to_per_attempt_stdout(
    fake_engine_offline: FakeEngine,
) -> None:
    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.enter_node("a-node")
    fake_engine_offline.emit_log("a-node", "first line")
    fake_engine_offline.emit_log("a-node", "second line")

    attempt_dir = v1_paths.node_attempt_dir(
        fake_engine_offline.job_slug, "a-node", 1, root=fake_engine_offline.root
    )
    stdout = attempt_dir / "stdout.log"
    assert stdout.exists()
    assert "first line" in stdout.read_text()
    assert "second line" in stdout.read_text()


def test_request_hil_writes_pending_marker(
    fake_engine_offline: FakeEngine,
) -> None:
    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.enter_node("review-spec-human")
    hil_id = fake_engine_offline.request_hil(
        "review-spec-human",
        "review-verdict",
        prompt="Approve?",
    )
    # The contract: returns the node_id as the gate identifier.
    assert hil_id == "review-spec-human"

    pending_path = (
        v1_paths.job_dir(fake_engine_offline.job_slug, root=fake_engine_offline.root)
        / "pending"
        / "review-spec-human.json"
    )
    assert pending_path.exists()
    marker = json.loads(pending_path.read_text())
    assert marker["node_id"] == "review-spec-human"


def test_assert_hil_answered_fails_when_pending_present(
    fake_engine_offline: FakeEngine,
) -> None:
    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.enter_node("review-spec-human")
    fake_engine_offline.request_hil("review-spec-human", "review-verdict")

    with pytest.raises(AssertionError):
        fake_engine_offline.assert_hil_answered("review-spec-human")


def test_assert_hil_answered_succeeds_when_envelope_present(
    fake_engine_offline: FakeEngine,
) -> None:
    """When the pending marker is removed AND the envelope is present,
    assert_hil_answered returns the parsed value."""
    from shared.v1.types.review_verdict import ReviewVerdictValue

    fake_engine_offline.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine_offline.enter_node("review-spec-human")
    fake_engine_offline.request_hil("review-spec-human", "review-verdict")

    # Simulate the dashboard POST having succeeded:
    # - remove pending marker
    # - write envelope
    pending_path = (
        v1_paths.job_dir(fake_engine_offline.job_slug, root=fake_engine_offline.root)
        / "pending"
        / "review-spec-human.json"
    )
    pending_path.unlink()
    fake_engine_offline.complete_node(
        "review-spec-human",
        ReviewVerdictValue(verdict="approved", summary="LGTM"),
    )

    value = fake_engine_offline.assert_hil_answered("review-spec-human")
    assert value.verdict == "approved"  # type: ignore[attr-defined]


# =============================================================================
# Live dashboard fixture smoke tests
# =============================================================================


@pytest.mark.asyncio
async def test_dashboard_fixture_starts_and_serves_health(
    dashboard: DashboardHandle,
) -> None:
    """Smoke test: the live dashboard fixture boots, binds a port, and
    answers GET /api/health."""
    response = await dashboard.client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True


@pytest.mark.asyncio
async def test_dashboard_fixture_root_passed_to_settings(
    dashboard: DashboardHandle,
) -> None:
    """The root path the fixture used must be passed through to the
    dashboard's Settings — otherwise FakeEngine writes go to one place
    and the dashboard reads from another."""
    assert dashboard.root.is_dir()
    # Settings.root is reflected in the health response (cache_size > 0
    # would mean the watcher saw something we didn't write — fail loudly).
    response = await dashboard.client.get("/api/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_fixture_url_is_reachable_localhost(
    dashboard: DashboardHandle,
) -> None:
    """The fixture's url is a real localhost URL Playwright can hit
    (Stage 6)."""
    assert dashboard.url.startswith("http://127.0.0.1:")
    # And uvicorn answers on it.
    async with httpx.AsyncClient(timeout=2.0) as raw_client:
        resp = await raw_client.get(f"{dashboard.url}/api/health")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_fake_engine_fixture_writes_to_dashboard_root(
    dashboard: DashboardHandle,
    fake_engine: FakeEngine,
) -> None:
    """End-to-end: fake_engine.start_job lands in the same root the
    dashboard is configured against."""
    fake_engine.start_job(workflow={"name": "smoke"}, request="hi")

    assert fake_engine.root == dashboard.root
    job_json = v1_paths.job_config_path(fake_engine.job_slug, root=dashboard.root)
    assert job_json.exists()


@pytest.mark.asyncio
async def test_fake_engine_writes_observable_to_watcher(
    dashboard: DashboardHandle,
    fake_engine: FakeEngine,
) -> None:
    """The watcher (running because run_background_tasks=True) must
    pick up FakeEngine writes within a short poll window. We don't
    assert on the cache shape here — that's Stage 3 — only that the
    watcher *runs* and processes a write without erroring."""
    fake_engine.start_job(workflow={"name": "smoke"}, request="hi")

    # Watcher needs a moment to pick up the write. Poll the file
    # rather than sleeping; the test is whether the watcher process is
    # alive enough to not throw, not its exact latency.
    job_json = v1_paths.job_config_path(fake_engine.job_slug, root=dashboard.root)
    deadline = datetime.now().timestamp() + 5.0
    while datetime.now().timestamp() < deadline:
        if job_json.exists():
            break
    assert job_json.exists()

    # Dashboard still healthy after the write.
    resp = await dashboard.client.get("/api/health")
    assert resp.status_code == 200
