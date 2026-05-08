"""Smoke tests for v2 dashboard API.

The test client uses the fake runner so no real claude is spawned.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hammock_v2.engine import paths


def test_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["version"] == "v2"


def test_workflows_endpoint_lists_fix_bug(client: TestClient) -> None:
    r = client.get("/api/workflows")
    assert r.status_code == 200
    names = [w["name"] for w in r.json()["workflows"]]
    assert "fix-bug" in names


def test_workflow_detail(client: TestClient) -> None:
    r = client.get("/api/workflows/fix-bug")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "fix-bug"
    ids = [n["id"] for n in body["nodes"]]
    assert "open-pr" in ids


def test_workflow_detail_404(client: TestClient) -> None:
    r = client.get("/api/workflows/nonexistent")
    assert r.status_code == 404


def test_jobs_list_empty(client: TestClient) -> None:
    r = client.get("/api/jobs")
    assert r.status_code == 200
    assert r.json() == {"jobs": []}


def test_job_404(client: TestClient) -> None:
    r = client.get("/api/jobs/no-such-job")
    assert r.status_code == 404


def test_node_404(client: TestClient) -> None:
    r = client.get("/api/jobs/no-such-job/nodes/no-node")
    assert r.status_code == 404


def test_human_decision_404(client: TestClient) -> None:
    r = client.post(
        "/api/jobs/no-job/nodes/no-node/human_decision",
        json={"decision": "approved"},
    )
    assert r.status_code == 404


def test_human_decision_invalid_payload(client: TestClient, hammock_v2_root: Path) -> None:
    # Set up a node dir manually
    paths.ensure_job_layout("test-slug", root=hammock_v2_root)
    paths.ensure_node_layout("test-slug", "n", root=hammock_v2_root)
    r = client.post(
        "/api/jobs/test-slug/nodes/n/human_decision",
        json={"decision": "bogus"},
    )
    assert r.status_code == 400


def test_human_decision_writes_file(client: TestClient, hammock_v2_root: Path) -> None:
    paths.ensure_job_layout("test-slug", root=hammock_v2_root)
    paths.ensure_node_layout("test-slug", "n", root=hammock_v2_root)
    r = client.post(
        "/api/jobs/test-slug/nodes/n/human_decision",
        json={"decision": "approved", "comment": "looks good"},
    )
    assert r.status_code == 200
    decision_path = paths.node_human_decision("test-slug", "n", root=hammock_v2_root)
    assert decision_path.is_file()
    text = decision_path.read_text()
    assert "decision: approved" in text
    assert "looks good" in text


def test_submit_job_then_list(client: TestClient, hammock_v2_root: Path) -> None:
    """Run a fake-mode submit through the API and verify the job dir
    materializes via the run_job CLI."""
    r = client.post(
        "/api/jobs",
        json={"workflow": "fix-bug", "request": "fix the lemon bug"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    slug = body["slug"]
    assert slug.startswith("2026-")
    assert "fix-bug" in slug

    # The orchestrator subprocess writes job.md eventually; in fake
    # mode this is essentially synchronous (couple hundred ms). Poll.
    import time

    job_md = paths.job_md(slug, root=hammock_v2_root)
    for _ in range(40):
        if job_md.is_file():
            text = job_md.read_text()
            if "state: completed" in text or "state: failed" in text:
                break
        time.sleep(0.25)

    listed = client.get("/api/jobs").json()["jobs"]
    assert any(j["slug"] == slug for j in listed)


def test_chat_endpoint_empty(client: TestClient) -> None:
    """Even when the job dir doesn't exist, chat endpoint returns empty
    rather than 500."""
    r = client.get("/api/jobs/no-job/nodes/no-node/chat")
    assert r.status_code == 200
    assert r.json() == {"turns": [], "has_chat": False}


def test_orchestrator_chat_endpoint_empty(client: TestClient) -> None:
    r = client.get("/api/jobs/no-job/orchestrator/chat")
    assert r.status_code == 200
    assert r.json() == {"turns": [], "has_chat": False}
