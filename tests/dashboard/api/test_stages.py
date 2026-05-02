"""Route tests for stage detail and the active-stages strip."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


class TestStageDetail:
    def test_happy_path(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/jobs/alpha-job-1/stages/implement")
        assert r.status_code == 200
        body = r.json()
        assert body["stage"]["stage_id"] == "implement"
        assert body["job_slug"] == "alpha-job-1"
        assert [t["task_id"] for t in body["tasks"]] == ["task-1"]

    def test_404_unknown_stage(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/jobs/alpha-job-1/stages/nope")
        assert r.status_code == 404

    def test_404_unknown_job(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/jobs/nope/stages/design")
        assert r.status_code == 404


class TestActiveStages:
    def test_returns_200(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/active-stages")
        assert r.status_code == 200

    def test_returns_running_and_attention_only(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/active-stages")
        states = {item["state"] for item in r.json()}
        assert states <= {"RUNNING", "ATTENTION_NEEDED"}
        assert states == {"RUNNING", "ATTENTION_NEEDED"}

    def test_empty_when_no_active(self, tmp_path: Path) -> None:
        # An empty root has no active stages.
        from dashboard.app import create_app
        from dashboard.settings import Settings

        client = TestClient(create_app(Settings(root=tmp_path)))
        with client:
            r = client.get("/api/active-stages")
        assert r.status_code == 200
        assert r.json() == []
