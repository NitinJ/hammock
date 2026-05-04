"""Shared fixtures for dashboard tests.

Provides a populated hammock-root layout used by both the projection unit
tests and the route-level TestClient tests. Building a single fixture here
keeps the suites in sync — every endpoint is exercised against the same
data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.settings import Settings
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import (
    AskQuestion,
    HilItem,
    JobConfig,
    JobState,
    ProjectConfig,
    ReviewQuestion,
    StageRun,
    StageState,
    TaskRecord,
    TaskState,
)


def _ts(offset_minutes: int = 0) -> datetime:
    return datetime(2026, 5, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=offset_minutes)


def _make_project(slug: str, *, doctor: str | None = "pass") -> ProjectConfig:
    return ProjectConfig(
        slug=slug,
        name=slug,
        repo_path=f"/tmp/{slug}",
        remote_url=f"https://github.com/example/{slug}",
        default_branch="main",
        created_at=_ts(),
        last_health_check_at=_ts() if doctor else None,
        last_health_check_status=doctor,  # type: ignore[arg-type]
    )


def _make_job(*, slug: str, project: str, state: JobState, t_offset: int = 0) -> JobConfig:
    return JobConfig(
        job_id=f"id-{slug}",
        job_slug=slug,
        project_slug=project,
        job_type="fix-bug",
        created_at=_ts(t_offset),
        created_by="nitin",
        state=state,
    )


def _make_stage(
    *,
    stage_id: str,
    state: StageState,
    started_offset: int | None = None,
    ended_offset: int | None = None,
    cost: float = 0.0,
) -> StageRun:
    return StageRun(
        stage_id=stage_id,
        attempt=1,
        state=state,
        started_at=_ts(started_offset) if started_offset is not None else None,
        ended_at=_ts(ended_offset) if ended_offset is not None else None,
        cost_accrued=cost,
    )


def _write_event(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(event) + "\n")


@pytest.fixture
def populated_root(tmp_path: Path) -> Path:
    """A hammock root pre-populated with two projects, three jobs, four stages,
    two HIL items, plus cost events for one job/stage.
    """
    # Projects
    alpha = _make_project("alpha", doctor="pass")
    beta = _make_project("beta", doctor="warn")
    atomic_write_json(paths.project_json("alpha", root=tmp_path), alpha)
    atomic_write_json(paths.project_json("beta", root=tmp_path), beta)

    # Jobs
    j1 = _make_job(slug="alpha-job-1", project="alpha", state=JobState.STAGES_RUNNING, t_offset=0)
    j2 = _make_job(slug="alpha-job-2", project="alpha", state=JobState.COMPLETED, t_offset=10)
    j3 = _make_job(slug="beta-job-1", project="beta", state=JobState.SUBMITTED, t_offset=20)
    for j in (j1, j2, j3):
        atomic_write_json(paths.job_json(j.job_slug, root=tmp_path), j)

    # Stages on alpha-job-1
    s_design = _make_stage(
        stage_id="design",
        state=StageState.SUCCEEDED,
        started_offset=1,
        ended_offset=5,
        cost=1.25,
    )
    s_implement = _make_stage(
        stage_id="implement",
        state=StageState.RUNNING,
        started_offset=6,
        cost=0.75,
    )
    s_attention = _make_stage(
        stage_id="review",
        state=StageState.ATTENTION_NEEDED,
        started_offset=7,
        cost=0.10,
    )
    for s in (s_design, s_implement, s_attention):
        atomic_write_json(paths.stage_json(j1.job_slug, s.stage_id, root=tmp_path), s)

    # One stage on alpha-job-2 (completed)
    s_done = _make_stage(
        stage_id="done-stage",
        state=StageState.SUCCEEDED,
        started_offset=11,
        ended_offset=15,
        cost=2.0,
    )
    atomic_write_json(paths.stage_json(j2.job_slug, s_done.stage_id, root=tmp_path), s_done)

    # HIL items — one open (ask) on alpha-job-1, one open (review) on beta-job-1
    hil_open_ask = HilItem(
        id="hil-open-ask",
        kind="ask",
        stage_id="design",
        created_at=_ts(2),
        status="awaiting",
        question=AskQuestion(text="Use Argon2id?"),
    )
    hil_open_review = HilItem(
        id="hil-open-review",
        kind="review",
        stage_id="design-spec-review-human",
        created_at=_ts(21),
        status="awaiting",
        question=ReviewQuestion(target="design-spec.md", prompt="Approve?"),
    )
    atomic_write_json(
        paths.hil_item_path(j1.job_slug, hil_open_ask.id, root=tmp_path), hil_open_ask
    )
    atomic_write_json(
        paths.hil_item_path(j3.job_slug, hil_open_review.id, root=tmp_path), hil_open_review
    )

    # Cost events on alpha-job-1 — both job-level and stage-level files
    job_events = paths.job_events_jsonl(j1.job_slug, root=tmp_path)
    _write_event(
        job_events,
        {
            "seq": 1,
            "timestamp": _ts(2).isoformat(),
            "event_type": "cost_accrued",
            "source": "agent0",
            "job_id": j1.job_id,
            "stage_id": "design",
            "payload": {"delta_usd": 0.5, "delta_tokens": 12000, "agent_ref": "design-spec-writer"},
        },
    )
    _write_event(
        job_events,
        {
            "seq": 2,
            "timestamp": _ts(3).isoformat(),
            "event_type": "cost_accrued",
            "source": "agent0",
            "job_id": j1.job_id,
            "stage_id": "design",
            "payload": {
                "delta_usd": 0.75,
                "delta_tokens": 18000,
                "agent_ref": "design-spec-writer",
            },
        },
    )
    _write_event(
        job_events,
        {
            "seq": 3,
            "timestamp": _ts(6).isoformat(),
            "event_type": "cost_accrued",
            "source": "agent0",
            "job_id": j1.job_id,
            "stage_id": "implement",
            "payload": {"delta_usd": 1.0, "delta_tokens": 24000, "agent_ref": "implementer"},
        },
    )
    # A non-cost event so the folder skips it
    _write_event(
        job_events,
        {
            "seq": 4,
            "timestamp": _ts(6).isoformat(),
            "event_type": "stage_state_transition",
            "source": "job_driver",
            "job_id": j1.job_id,
            "payload": {"from": "READY", "to": "RUNNING"},
        },
    )

    # Stage-level events for the 'design' stage
    stage_events = paths.stage_events_jsonl(j1.job_slug, "design", root=tmp_path)
    _write_event(
        stage_events,
        {
            "seq": 1,
            "timestamp": _ts(2).isoformat(),
            "event_type": "cost_accrued",
            "source": "agent0",
            "job_id": j1.job_id,
            "stage_id": "design",
            "payload": {"delta_usd": 0.5, "delta_tokens": 12000, "agent_ref": "design-spec-writer"},
        },
    )
    _write_event(
        stage_events,
        {
            "seq": 2,
            "timestamp": _ts(3).isoformat(),
            "event_type": "cost_accrued",
            "source": "agent0",
            "job_id": j1.job_id,
            "stage_id": "design",
            "payload": {
                "delta_usd": 0.75,
                "delta_tokens": 18000,
                "agent_ref": "design-spec-writer",
            },
        },
    )

    # A task record on alpha-job-1/implement so stage_detail tests have data
    task_path = paths.task_json(j1.job_slug, "implement", "task-1", root=tmp_path)
    task_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        task_path,
        TaskRecord(
            task_id="task-1",
            stage_id="implement",
            state=TaskState.RUNNING,
            created_at=_ts(6),
        ),
    )

    # An artifact on alpha-job-1
    artifact = paths.job_dir(j1.job_slug, root=tmp_path) / "design-spec.md"
    artifact.write_text("# design spec\n\ncontent here\n")

    return tmp_path


@pytest.fixture
def client(populated_root: Path) -> TestClient:
    # `run_background_tasks=False` prevents the supervisor + watcher +
    # MCP manager from racing test setup (they fire on lifespan
    # startup; pre-seeded jobs without heartbeats would trigger a
    # spurious respawn that conflicts with the test's API calls).
    app = create_app(Settings(root=populated_root, run_background_tasks=False))
    return TestClient(app)
