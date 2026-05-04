"""Tests for Stage-15 stage action endpoints.

POST /api/jobs/{job_slug}/stages/{stage_id}/chat    — push a nudge
POST /api/jobs/{job_slug}/stages/{stage_id}/cancel  — write cancel command
POST /api/jobs/{job_slug}/stages/{stage_id}/restart — re-spawn driver
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.settings import Settings
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import JobConfig, JobState, StageRun, StageState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(offset: int = 0) -> datetime:
    return datetime(2026, 5, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=offset)


def _job(slug: str) -> JobConfig:
    return JobConfig(
        job_id=f"id-{slug}",
        job_slug=slug,
        project_slug="alpha",
        job_type="fix-bug",
        created_at=_ts(),
        created_by="test",
        state=JobState.STAGES_RUNNING,
    )


def _stage(stage_id: str, *, restart_count: int = 0) -> StageRun:
    return StageRun(
        stage_id=stage_id,
        attempt=1 + restart_count,
        state=StageState.RUNNING,
        started_at=_ts(1),
        restart_count=restart_count,
    )


@pytest.fixture
def stage_root(tmp_path: Path) -> Path:
    """A minimal hammock root with one job and one running stage."""
    job = _job("test-job-1")
    atomic_write_json(paths.job_json(job.job_slug, root=tmp_path), job)
    stage = _stage("implement")
    atomic_write_json(paths.stage_json(job.job_slug, stage.stage_id, root=tmp_path), stage)
    return tmp_path


@pytest.fixture
def client(stage_root: Path) -> TestClient:
    # Disable background tasks (supervisor scan would race with the
    # restart-endpoint tests). v0 alignment Plan #7.
    return TestClient(create_app(Settings(root=stage_root, run_background_tasks=False)))


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------


class TestStageChat:
    def test_happy_path_returns_200(self, client: TestClient, stage_root: Path) -> None:
        with client:
            r = client.post(
                "/api/jobs/test-job-1/stages/implement/chat",
                json={"text": "use argon2id please"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["seq"] == 0
        assert body["text"] == "use argon2id please"
        assert body["kind"] == "chat"
        assert "timestamp" in body

    def test_nudge_written_to_disk(self, client: TestClient, stage_root: Path) -> None:
        with client:
            client.post(
                "/api/jobs/test-job-1/stages/implement/chat",
                json={"text": "also add tests"},
            )
        nudge_path = paths.stage_nudges_jsonl("test-job-1", "implement", root=stage_root)
        assert nudge_path.exists()
        lines = [line for line in nudge_path.read_text().splitlines() if line.strip()]
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["text"] == "also add tests"
        assert entry["source"] == "human"
        assert entry["kind"] == "chat"

    def test_sequential_nudges_increment_seq(self, client: TestClient, stage_root: Path) -> None:
        with client:
            r1 = client.post(
                "/api/jobs/test-job-1/stages/implement/chat",
                json={"text": "first"},
            )
            r2 = client.post(
                "/api/jobs/test-job-1/stages/implement/chat",
                json={"text": "second"},
            )
        assert r1.json()["seq"] == 0
        assert r2.json()["seq"] == 1

    def test_empty_text_rejected(self, client: TestClient) -> None:
        with client:
            r = client.post(
                "/api/jobs/test-job-1/stages/implement/chat",
                json={"text": ""},
            )
        assert r.status_code == 422

    def test_missing_text_field_rejected(self, client: TestClient) -> None:
        with client:
            r = client.post(
                "/api/jobs/test-job-1/stages/implement/chat",
                json={},
            )
        assert r.status_code == 422

    def test_unknown_job_returns_404(self, client: TestClient) -> None:
        with client:
            r = client.post(
                "/api/jobs/no-such-job/stages/implement/chat",
                json={"text": "hello"},
            )
        assert r.status_code == 404

    def test_unknown_stage_returns_404(self, client: TestClient) -> None:
        with client:
            r = client.post(
                "/api/jobs/test-job-1/stages/no-such-stage/chat",
                json={"text": "hello"},
            )
        assert r.status_code == 404

    def test_terminal_stage_returns_409(self, tmp_path: Path) -> None:
        job = _job("test-job-term")
        atomic_write_json(paths.job_json(job.job_slug, root=tmp_path), job)
        stage = StageRun(
            stage_id="implement",
            attempt=1,
            state=StageState.SUCCEEDED,
            started_at=_ts(1),
            restart_count=0,
        )
        atomic_write_json(paths.stage_json(job.job_slug, stage.stage_id, root=tmp_path), stage)
        with TestClient(create_app(Settings(root=tmp_path))) as c:
            r = c.post("/api/jobs/test-job-term/stages/implement/chat", json={"text": "hi"})
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# POST /cancel
# ---------------------------------------------------------------------------


class TestStageCancel:
    def test_returns_200(self, client: TestClient) -> None:
        with client:
            r = client.post("/api/jobs/test-job-1/stages/implement/cancel")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_writes_cancel_command_file(self, client: TestClient, stage_root: Path) -> None:
        with client:
            client.post("/api/jobs/test-job-1/stages/implement/cancel")
        action_path = paths.job_human_action("test-job-1", root=stage_root)
        assert action_path.exists()
        payload = json.loads(action_path.read_text())
        assert payload["command"] == "cancel"

    def test_unknown_job_returns_404(self, client: TestClient) -> None:
        with client:
            r = client.post("/api/jobs/no-such-job/stages/implement/cancel")
        assert r.status_code == 404

    def test_unknown_stage_returns_404(self, client: TestClient) -> None:
        with client:
            r = client.post("/api/jobs/test-job-1/stages/no-such-stage/cancel")
        assert r.status_code == 404

    def test_terminal_stage_returns_409(self, tmp_path: Path) -> None:
        job = _job("test-job-term2")
        atomic_write_json(paths.job_json(job.job_slug, root=tmp_path), job)
        stage = StageRun(
            stage_id="implement",
            attempt=1,
            state=StageState.FAILED,
            started_at=_ts(1),
            restart_count=0,
        )
        atomic_write_json(paths.stage_json(job.job_slug, stage.stage_id, root=tmp_path), stage)
        with TestClient(create_app(Settings(root=tmp_path))) as c:
            r = c.post("/api/jobs/test-job-term2/stages/implement/cancel")
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# POST /restart
# ---------------------------------------------------------------------------


class TestStageRestart:
    def test_returns_200_and_pid(self, client: TestClient, stage_root: Path) -> None:
        with client:
            r = client.post("/api/jobs/test-job-1/stages/implement/restart")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["job_driver_pid"], int)
        assert body["job_driver_pid"] > 0

    def test_pid_file_written(self, client: TestClient, stage_root: Path) -> None:
        with client:
            client.post("/api/jobs/test-job-1/stages/implement/restart")
        pid_path = paths.job_driver_pid("test-job-1", root=stage_root)
        assert pid_path.exists()
        pid = int(pid_path.read_text().strip())
        assert pid > 0

    def test_restart_count_exceeded_returns_409(self, tmp_path: Path) -> None:
        job = _job("test-job-2")
        atomic_write_json(paths.job_json(job.job_slug, root=tmp_path), job)
        # Stage with restart_count at the max (3)
        exhausted = _stage("implement", restart_count=3)
        atomic_write_json(
            paths.stage_json(job.job_slug, exhausted.stage_id, root=tmp_path), exhausted
        )
        client = TestClient(create_app(Settings(root=tmp_path)))
        with client:
            r = client.post("/api/jobs/test-job-2/stages/implement/restart")
        assert r.status_code == 409

    def test_unknown_job_returns_404(self, client: TestClient) -> None:
        with client:
            r = client.post("/api/jobs/no-such-job/stages/implement/restart")
        assert r.status_code == 404

    def test_unknown_stage_returns_404(self, client: TestClient) -> None:
        with client:
            r = client.post("/api/jobs/test-job-1/stages/no-such-stage/restart")
        assert r.status_code == 404
