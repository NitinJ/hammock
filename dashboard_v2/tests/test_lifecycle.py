"""Tests for the job lifecycle primitives + HTTP endpoints."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard_v2.api.app import create_app
from dashboard_v2.jobs import lifecycle as lc
from hammock_v2.engine import paths


def _seed_job(root: Path, slug: str, state: str = "running") -> Path:
    """Lay down a minimal job dir (job.md + control.md + nodes/) so the
    projection can resolve a summary for the lifecycle helpers."""
    jd = paths.ensure_job_layout(slug, root=root)
    now = _dt.datetime.now(_dt.UTC).isoformat()
    paths.job_md(slug, root=root).write_text(
        "---\n"
        f"slug: {slug}\n"
        "workflow: fix-bug\n"
        f"state: {state}\n"
        f"submitted_at: {now}\n"
        f"started_at: {now}\n"
        "---\n\n"
        "## Request\n\nfix it\n"
    )
    paths.workflow_yaml(slug, root=root).write_text(
        "name: fix-bug\nnodes:\n  - id: write-bug-report\n    prompt: write-bug-report\n"
    )
    paths.control_md(slug, root=root).write_text(
        f"---\nstate: running\nrequested_at: {now}\nrequested_by: submit\n---\n"
    )
    nd = paths.node_dir(slug, "write-bug-report", root=root)
    nd.mkdir(parents=True, exist_ok=True)
    (nd / "state.md").write_text("---\nstate: pending\n---\n")
    return jd


def test_pause_writes_control_md(tmp_path: Path) -> None:
    _seed_job(tmp_path, "j-1")
    res = lc.pause_job("j-1", root=tmp_path)
    assert res["controlled_state"] == "paused"
    assert "state: paused" in paths.control_md("j-1", root=tmp_path).read_text()


def test_pause_rejects_terminal_job(tmp_path: Path) -> None:
    _seed_job(tmp_path, "j-2", state="completed")
    with pytest.raises(lc.LifecycleError):
        lc.pause_job("j-2", root=tmp_path)


def test_pause_rejects_unknown_job(tmp_path: Path) -> None:
    with pytest.raises(lc.LifecycleError) as exc:
        lc.pause_job("nope", root=tmp_path)
    assert exc.value.status == 404


def test_resume_only_when_paused(tmp_path: Path) -> None:
    _seed_job(tmp_path, "j-3")
    # Initially running → resume should reject.
    with pytest.raises(lc.LifecycleError):
        lc.resume_job("j-3", root=tmp_path)
    # Pause first.
    lc.pause_job("j-3", root=tmp_path)
    # Now resume should succeed.
    res = lc.resume_job("j-3", root=tmp_path)
    assert res["controlled_state"] == "running"
    assert "state: running" in paths.control_md("j-3", root=tmp_path).read_text()


def test_stop_writes_cancel_and_returns_no_pidfile(tmp_path: Path) -> None:
    _seed_job(tmp_path, "j-4")
    # No pid file present in seeded dir.
    res = lc.stop_job("j-4", root=tmp_path)
    assert res["controlled_state"] == "cancelled"
    assert res["killed"] == "no_pidfile"
    assert "state: cancelled" in paths.control_md("j-4", root=tmp_path).read_text()


def test_stop_skips_kill_when_pid_already_dead(tmp_path: Path) -> None:
    _seed_job(tmp_path, "j-5")
    # Use pid 999999 which is extremely unlikely to be alive.
    paths.orchestrator_pid_file("j-5", root=tmp_path).write_text("999999")
    res = lc.stop_job("j-5", root=tmp_path, grace_seconds=0.5, sleep=0.1)
    assert res["killed"] == "already_dead"


def test_stop_rejects_terminal_job(tmp_path: Path) -> None:
    _seed_job(tmp_path, "j-6", state="failed")
    with pytest.raises(lc.LifecycleError):
        lc.stop_job("j-6", root=tmp_path)


def test_delete_only_when_terminal(tmp_path: Path) -> None:
    _seed_job(tmp_path, "j-7")
    with pytest.raises(lc.LifecycleError) as exc:
        lc.delete_job("j-7", root=tmp_path)
    assert exc.value.status == 409


def test_delete_removes_dir_when_terminal(tmp_path: Path) -> None:
    _seed_job(tmp_path, "j-8", state="completed")
    res = lc.delete_job("j-8", root=tmp_path)
    assert res["deleted"] == "true"
    assert not paths.job_dir("j-8", root=tmp_path).exists()


# ---------------------- HTTP layer ----------------------


def _client_with_root(monkeypatch: pytest.MonkeyPatch, root: Path) -> TestClient:
    monkeypatch.setenv("HAMMOCK_V2_ROOT", str(root))
    app = create_app()
    return TestClient(app)


def test_http_pause_resume_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_job(tmp_path, "h-1")
    client = _client_with_root(monkeypatch, tmp_path)
    r = client.post("/api/jobs/h-1/pause")
    assert r.status_code == 200, r.text
    assert r.json()["controlled_state"] == "paused"
    r = client.post("/api/jobs/h-1/resume")
    assert r.status_code == 200, r.text
    assert r.json()["controlled_state"] == "running"


def test_http_stop_returns_200(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_job(tmp_path, "h-2")
    client = _client_with_root(monkeypatch, tmp_path)
    r = client.post("/api/jobs/h-2/stop")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["controlled_state"] == "cancelled"


def test_http_delete_409_when_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_job(tmp_path, "h-3")
    client = _client_with_root(monkeypatch, tmp_path)
    r = client.delete("/api/jobs/h-3")
    assert r.status_code == 409, r.text


def test_http_delete_200_when_terminal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _seed_job(tmp_path, "h-4", state="cancelled")
    client = _client_with_root(monkeypatch, tmp_path)
    r = client.delete("/api/jobs/h-4")
    assert r.status_code == 200, r.text
    assert not paths.job_dir("h-4", root=tmp_path).exists()
