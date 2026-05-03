"""Tests for ``dashboard.state.cache``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dashboard.state.cache import Cache, ChangeKind, classify_path
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import JobConfig, JobState, StageRun, StageState
from tests.shared.factories import (
    make_ask_hil_item,
    make_job,
    make_project,
    make_stage_run,
)

# ---------------------------------------------------------------------------
# classify_path
# ---------------------------------------------------------------------------


def test_classify_project(tmp_path: Path) -> None:
    p = paths.project_json("figur-backend-v2", root=tmp_path)
    c = classify_path(p, tmp_path)
    assert c.kind == "project"
    assert c.project_slug == "figur-backend-v2"


def test_classify_job(tmp_path: Path) -> None:
    p = paths.job_json("fix-login", root=tmp_path)
    c = classify_path(p, tmp_path)
    assert c.kind == "job"
    assert c.job_slug == "fix-login"


def test_classify_stage(tmp_path: Path) -> None:
    p = paths.stage_json("fix-login", "design", root=tmp_path)
    c = classify_path(p, tmp_path)
    assert c.kind == "stage"
    assert c.job_slug == "fix-login"
    assert c.stage_id == "design"


def test_classify_hil(tmp_path: Path) -> None:
    p = paths.hil_item_path("fix-login", "ask-001", root=tmp_path)
    c = classify_path(p, tmp_path)
    assert c.kind == "hil"
    assert c.job_slug == "fix-login"
    assert c.hil_id == "ask-001"


def test_classify_unknown_paths(tmp_path: Path) -> None:
    # raw artifact, event log, side files
    assert classify_path(tmp_path / "jobs/x/events.jsonl", tmp_path).kind == "unknown"
    assert classify_path(tmp_path / "jobs/x/heartbeat", tmp_path).kind == "unknown"
    assert classify_path(tmp_path / "jobs/x/design-spec.md", tmp_path).kind == "unknown"
    assert classify_path(tmp_path / "outside", Path("/elsewhere")).kind == "unknown"


# ---------------------------------------------------------------------------
# Cache.bootstrap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_empty_root(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path)
    assert cache.list_projects() == []
    assert cache.list_jobs() == []
    assert cache.size() == {"projects": 0, "jobs": 0, "stages": 0, "hil": 0}


@pytest.mark.asyncio
async def test_bootstrap_missing_root(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path / "does-not-exist")
    assert cache.list_projects() == []


@pytest.mark.asyncio
async def test_bootstrap_reads_state_files(tmp_path: Path) -> None:
    project = make_project()
    job = make_job()
    stage = make_stage_run()
    hil = make_ask_hil_item()

    atomic_write_json(paths.project_json(project.slug, root=tmp_path), project)
    atomic_write_json(paths.job_json(job.job_slug, root=tmp_path), job)
    atomic_write_json(paths.stage_json(job.job_slug, stage.stage_id, root=tmp_path), stage)
    atomic_write_json(paths.hil_item_path(job.job_slug, hil.id, root=tmp_path), hil)

    cache = await Cache.bootstrap(tmp_path)
    assert cache.get_project(project.slug) == project
    assert cache.get_job(job.job_slug) == job
    assert cache.get_stage(job.job_slug, stage.stage_id) == stage
    assert cache.get_hil(hil.id) == hil


@pytest.mark.asyncio
async def test_bootstrap_skips_unknown_files(tmp_path: Path) -> None:
    # Stray files in the root must not crash the cache.
    (tmp_path / "stray.json").write_text('{"random": "garbage"}')
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "deeply-nested" / "noise").mkdir(parents=True)
    (tmp_path / "jobs" / "deeply-nested" / "noise" / "file.json").write_text("{}")

    cache = await Cache.bootstrap(tmp_path)
    assert cache.list_jobs() == []


@pytest.mark.asyncio
async def test_bootstrap_invalid_json_logged_not_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    bad = paths.job_json("bad", root=tmp_path)
    bad.parent.mkdir(parents=True)
    bad.write_text("{not-json")

    with caplog.at_level("WARNING"):
        cache = await Cache.bootstrap(tmp_path)

    assert cache.list_jobs() == []
    assert any("cannot parse" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# apply_change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_change_added_then_modified_then_deleted(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path)
    project = make_project()

    # Added
    atomic_write_json(paths.project_json(project.slug, root=tmp_path), project)
    cls = cache.apply_change(paths.project_json(project.slug, root=tmp_path), ChangeKind.ADDED)
    assert cls.kind == "project"
    assert cache.get_project(project.slug) == project

    # Modified
    renamed = project.model_copy(update={"name": "new-display-name"})
    atomic_write_json(paths.project_json(project.slug, root=tmp_path), renamed)
    cache.apply_change(paths.project_json(project.slug, root=tmp_path), ChangeKind.MODIFIED)
    cached = cache.get_project(project.slug)
    assert cached is not None and cached.name == "new-display-name"

    # Deleted
    cache.apply_change(paths.project_json(project.slug, root=tmp_path), ChangeKind.DELETED)
    assert cache.get_project(project.slug) is None


@pytest.mark.asyncio
async def test_apply_change_ignores_unknown_paths(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path)
    cls = cache.apply_change(tmp_path / "stray.json", ChangeKind.MODIFIED)
    assert cls.kind == "unknown"
    assert cache.size() == {"projects": 0, "jobs": 0, "stages": 0, "hil": 0}


# ---------------------------------------------------------------------------
# list_*, get_*, scope filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_jobs_filter_by_project(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path)
    j1 = JobConfig(
        job_id="j1",
        job_slug="j1",
        project_slug="alpha",
        job_type="fix-bug",
        created_at=datetime(2026, 5, 2, tzinfo=UTC),
        created_by="x",
        state=JobState.SUBMITTED,
    )
    j2 = j1.model_copy(update={"job_id": "j2", "job_slug": "j2", "project_slug": "beta"})
    atomic_write_json(paths.job_json("j1", root=tmp_path), j1)
    atomic_write_json(paths.job_json("j2", root=tmp_path), j2)

    cache.apply_change(paths.job_json("j1", root=tmp_path), ChangeKind.ADDED)
    cache.apply_change(paths.job_json("j2", root=tmp_path), ChangeKind.ADDED)

    assert {j.job_slug for j in cache.list_jobs()} == {"j1", "j2"}
    assert {j.job_slug for j in cache.list_jobs(project_slug="alpha")} == {"j1"}
    assert {j.job_slug for j in cache.list_jobs(project_slug="missing")} == set()


@pytest.mark.asyncio
async def test_list_stages_per_job(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path)
    s1 = StageRun(stage_id="design", attempt=1, state=StageState.RUNNING)
    s2 = StageRun(stage_id="implement", attempt=1, state=StageState.PENDING)
    atomic_write_json(paths.stage_json("job-x", "design", root=tmp_path), s1)
    atomic_write_json(paths.stage_json("job-x", "implement", root=tmp_path), s2)

    cache.apply_change(paths.stage_json("job-x", "design", root=tmp_path), ChangeKind.ADDED)
    cache.apply_change(paths.stage_json("job-x", "implement", root=tmp_path), ChangeKind.ADDED)

    stage_ids = {s.stage_id for s in cache.list_stages("job-x")}
    assert stage_ids == {"design", "implement"}
    assert cache.list_stages("other-job") == []


@pytest.mark.asyncio
async def test_list_hil_filters(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path)
    h_awaiting = make_ask_hil_item()
    from shared.models.hil import AskAnswer

    h_answered = h_awaiting.model_copy(
        update={
            "id": "hil-007",
            "status": "answered",
            "answer": AskAnswer(text="yes", choice=None),
            "answered_at": datetime(2026, 5, 2, 1, 0, tzinfo=UTC),
        }
    )

    atomic_write_json(paths.hil_item_path("job-a", h_awaiting.id, root=tmp_path), h_awaiting)
    atomic_write_json(paths.hil_item_path("job-a", h_answered.id, root=tmp_path), h_answered)

    cache.apply_change(paths.hil_item_path("job-a", h_awaiting.id, root=tmp_path), ChangeKind.ADDED)
    cache.apply_change(paths.hil_item_path("job-a", h_answered.id, root=tmp_path), ChangeKind.ADDED)

    assert len(cache.list_hil()) == 2
    awaiting = cache.list_hil(status="awaiting")
    assert {h.id for h in awaiting} == {h_awaiting.id}
    answered = cache.list_hil(status="answered")
    assert {h.id for h in answered} == {h_answered.id}
    assert len(cache.list_hil(job_slug="job-a")) == 2
    assert cache.list_hil(job_slug="job-z") == []


# ---------------------------------------------------------------------------
# Performance sanity — bootstrap of 100 jobs <500ms (acceptance criterion)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_100_jobs_under_500ms(tmp_path: Path) -> None:
    import time

    project = make_project()
    atomic_write_json(paths.project_json(project.slug, root=tmp_path), project)
    base = make_job()
    for i in range(100):
        job = base.model_copy(update={"job_id": f"job-{i}", "job_slug": f"job-{i}"})
        atomic_write_json(paths.job_json(job.job_slug, root=tmp_path), job)

    t0 = time.monotonic()
    cache = await Cache.bootstrap(tmp_path)
    elapsed = time.monotonic() - t0
    assert len(cache.list_jobs()) == 100
    assert elapsed < 0.5, f"bootstrap took {elapsed:.3f}s, exceeds 500ms acceptance criterion"
