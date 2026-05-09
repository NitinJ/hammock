"""Tests for the orchestrator pseudo-node endpoints: events + 2-way messages."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _seed_job(root: Path, slug: str) -> Path:
    job_dir = root / "jobs" / slug
    (job_dir / "nodes").mkdir(parents=True, exist_ok=True)
    (job_dir / "job.md").write_text(
        "---\n"
        f"slug: {slug}\n"
        "workflow: test-flow\n"
        "state: running\n"
        "submitted_at: 2026-05-09T10:00:00+00:00\n"
        "started_at: 2026-05-09T10:00:01+00:00\n"
        "---\n\n"
        "## Request\n\nDo a thing.\n"
    )
    return job_dir


def test_orchestrator_events_404_when_no_job(client: TestClient) -> None:
    r = client.get("/api/jobs/nope/orchestrator/events")
    assert r.status_code == 404


def test_orchestrator_events_returns_chronological(client: TestClient, hammock_root: Path) -> None:
    job_dir = _seed_job(hammock_root, "j1")
    node_dir = job_dir / "nodes" / "alpha"
    node_dir.mkdir()
    (node_dir / "state.md").write_text(
        "---\n"
        "state: succeeded\n"
        "started_at: 2026-05-09T10:00:05+00:00\n"
        "finished_at: 2026-05-09T10:00:10+00:00\n"
        "---\n"
    )
    r = client.get("/api/jobs/j1/orchestrator/events")
    assert r.status_code == 200
    events = r.json()["events"]
    kinds = [e["kind"] for e in events]
    # Should at least contain job_submitted, job_started, node_started, node_succeeded
    assert "job_submitted" in kinds
    assert "job_started" in kinds
    assert "node_started" in kinds
    assert "node_succeeded" in kinds
    # And be sorted ascending by timestamp
    timestamps = [e["at"] for e in events if e["at"]]
    assert timestamps == sorted(timestamps)


def test_orchestrator_messages_empty_initially(client: TestClient, hammock_root: Path) -> None:
    _seed_job(hammock_root, "j2")
    r = client.get("/api/jobs/j2/orchestrator/messages")
    assert r.status_code == 200
    assert r.json()["messages"] == []


def test_post_orchestrator_message_appends(client: TestClient, hammock_root: Path) -> None:
    _seed_job(hammock_root, "j3")
    r = client.post(
        "/api/jobs/j3/orchestrator/messages",
        json={"text": "Please skip the implement node."},
    )
    assert r.status_code == 200, r.text
    msg = r.json()["message"]
    assert msg["from"] == "operator"
    assert msg["id"] == "msg-1"
    # Disk
    queue = hammock_root / "jobs" / "j3" / "orchestrator_messages.jsonl"
    assert queue.is_file()
    line = queue.read_text().splitlines()[0]
    assert json.loads(line)["text"] == "Please skip the implement node."

    # Second message gets msg-2
    r2 = client.post(
        "/api/jobs/j3/orchestrator/messages",
        json={"text": "Status please?"},
    )
    assert r2.status_code == 200
    assert r2.json()["message"]["id"] == "msg-2"

    # GET returns both
    listed = client.get("/api/jobs/j3/orchestrator/messages").json()["messages"]
    assert [m["id"] for m in listed] == ["msg-1", "msg-2"]


def test_post_orchestrator_message_404_when_no_job(client: TestClient) -> None:
    r = client.post(
        "/api/jobs/nope/orchestrator/messages",
        json={"text": "hi"},
    )
    assert r.status_code == 404


def test_post_orchestrator_message_rejects_empty(client: TestClient, hammock_root: Path) -> None:
    _seed_job(hammock_root, "j4")
    r = client.post(
        "/api/jobs/j4/orchestrator/messages",
        json={"text": "  "},
    )
    # Empty after strip; pydantic accepts (min_length=1 trips on length 0
    # only). The endpoint's append_orchestrator_message strips and
    # rejects whitespace-only.
    assert r.status_code in (400, 422)
