"""Tests for ``dashboard.watcher.tailer``.

The tailer is exercised against a fake watch stream rather than the real
``watchfiles.awatch`` to keep tests deterministic. End-to-end-against-real-fs
coverage lives in the smoke script (``scripts/manual-smoke-stage1.py``).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from watchfiles import Change

import json

from dashboard.state.cache import Cache, ChangeKind, ClassifiedPath
from dashboard.state.pubsub import InProcessPubSub
from dashboard.watcher.tailer import CacheChange, run, scopes_for, to_change_kind
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import Event
from tests.shared.factories import make_ask_hil_item, make_job, make_project, make_stage_run

# ---------------------------------------------------------------------------
# Pure-function bits
# ---------------------------------------------------------------------------


def test_to_change_kind() -> None:
    assert to_change_kind(Change.added) is ChangeKind.ADDED
    assert to_change_kind(Change.modified) is ChangeKind.MODIFIED
    assert to_change_kind(Change.deleted) is ChangeKind.DELETED


def test_scopes_for_global_always_included() -> None:
    cls = ClassifiedPath("project", project_slug="p1")
    assert "global" in scopes_for(cls)


def test_scopes_for_project() -> None:
    cls = ClassifiedPath("project", project_slug="p1")
    assert set(scopes_for(cls)) == {"global", "project:p1"}


def test_scopes_for_job() -> None:
    cls = ClassifiedPath("job", job_slug="j1")
    assert set(scopes_for(cls)) == {"global", "job:j1"}


def test_scopes_for_stage_includes_job_and_stage() -> None:
    cls = ClassifiedPath("stage", job_slug="j1", stage_id="design")
    assert set(scopes_for(cls)) == {"global", "job:j1", "stage:j1:design"}


def test_scopes_for_hil_attaches_to_job() -> None:
    cls = ClassifiedPath("hil", job_slug="j1", hil_id="ask-1")
    assert set(scopes_for(cls)) == {"global", "job:j1"}


def test_scopes_for_unknown_only_global() -> None:
    cls = ClassifiedPath("unknown")
    assert scopes_for(cls) == ["global"]


# ---------------------------------------------------------------------------
# Tailer integration — fake watch stream → cache + pubsub
# ---------------------------------------------------------------------------


async def _fake_stream(
    batches: list[set[tuple[Change, str]]],
) -> AsyncIterator[set[tuple[Change, str]]]:
    for b in batches:
        yield b


@pytest.mark.asyncio
async def test_tailer_updates_cache_and_publishes_for_project(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path)
    bus: InProcessPubSub[CacheChange] = InProcessPubSub()
    project = make_project()
    project_path = paths.project_json(project.slug, root=tmp_path)
    atomic_write_json(project_path, project)

    sub_global = bus.subscribe("global")
    sub_proj = bus.subscribe(f"project:{project.slug}")
    sub_other = bus.subscribe("project:other")

    batches = [{(Change.added, str(project_path))}]
    await run(cache, bus, _watcher=_fake_stream(batches))

    assert cache.get_project(project.slug) == project
    msg_g = await asyncio.wait_for(anext(sub_global), timeout=1.0)
    msg_p = await asyncio.wait_for(anext(sub_proj), timeout=1.0)
    assert msg_g.kind is ChangeKind.ADDED
    assert msg_p.classified.project_slug == project.slug
    # Other scope must not have received anything
    assert bus.subscriber_count("project:other") == 1
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(sub_other), timeout=0.1)


@pytest.mark.asyncio
async def test_tailer_handles_stage_change(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path)
    bus: InProcessPubSub[CacheChange] = InProcessPubSub()
    stage = make_stage_run()
    sp = paths.stage_json("job-x", stage.stage_id, root=tmp_path)
    atomic_write_json(sp, stage)

    sub_stage = bus.subscribe(f"stage:job-x:{stage.stage_id}")
    sub_job = bus.subscribe("job:job-x")

    await run(cache, bus, _watcher=_fake_stream([{(Change.added, str(sp))}]))

    assert cache.get_stage("job-x", stage.stage_id) == stage
    msg_stage = await asyncio.wait_for(anext(sub_stage), timeout=1.0)
    msg_job = await asyncio.wait_for(anext(sub_job), timeout=1.0)
    assert msg_stage.classified.stage_id == stage.stage_id
    assert msg_job.classified.job_slug == "job-x"


@pytest.mark.asyncio
async def test_tailer_deletion_removes_from_cache(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path)
    bus: InProcessPubSub[CacheChange] = InProcessPubSub()
    job = make_job()
    jp = paths.job_json(job.job_slug, root=tmp_path)
    atomic_write_json(jp, job)
    cache.apply_change(jp, ChangeKind.ADDED)
    assert cache.get_job(job.job_slug) is not None

    await run(cache, bus, _watcher=_fake_stream([{(Change.deleted, str(jp))}]))
    assert cache.get_job(job.job_slug) is None


@pytest.mark.asyncio
async def test_tailer_skips_unknown_paths(tmp_path: Path) -> None:
    cache = await Cache.bootstrap(tmp_path)
    bus: InProcessPubSub[CacheChange] = InProcessPubSub()
    sub = bus.subscribe("global")

    stray = tmp_path / "stray.json"
    stray.write_text("{}")

    await run(cache, bus, _watcher=_fake_stream([{(Change.added, str(stray))}]))

    # No publish for unknown paths
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(sub), timeout=0.1)
    assert cache.size() == {"projects": 0, "jobs": 0, "stages": 0, "hil": 0}


@pytest.mark.asyncio
async def test_tailer_continues_on_invalid_json(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    cache = await Cache.bootstrap(tmp_path)
    bus: InProcessPubSub[CacheChange] = InProcessPubSub()
    sub = bus.subscribe("global")

    # First file: invalid JSON. Second: valid HIL item.
    bad_path = paths.job_json("bad", root=tmp_path)
    bad_path.parent.mkdir(parents=True)
    bad_path.write_text("{not-json")

    hil = make_ask_hil_item()
    hil_path = paths.hil_item_path("good-job", hil.id, root=tmp_path)
    atomic_write_json(hil_path, hil)

    batch = {(Change.added, str(bad_path)), (Change.added, str(hil_path))}
    with caplog.at_level("WARNING"):
        await run(cache, bus, _watcher=_fake_stream([batch]))

    # Cache picks up the good one despite the bad one.
    assert cache.get_hil(hil.id) == hil
    # The good change still publishes.
    msg = await asyncio.wait_for(anext(sub), timeout=1.0)
    assert msg.classified.kind in {"job", "hil"}


# ---------------------------------------------------------------------------
# Stage 12.5 (A5): events.jsonl tail — typed Event records published to events_pubsub
# ---------------------------------------------------------------------------


def _make_event_line(seq: int, job_slug: str, stage_id: str | None = None) -> str:
    from datetime import UTC, datetime

    record = {
        "seq": seq,
        "timestamp": datetime(2026, 5, 2, 12, 0, tzinfo=UTC).isoformat(),
        "event_type": "cost_accrued",
        "source": "agent0",
        "job_id": f"id-{job_slug}",
        "stage_id": stage_id,
        "payload": {"delta_usd": 0.01 * seq, "delta_tokens": seq * 10},
    }
    return json.dumps(record)


@pytest.mark.asyncio
async def test_tailer_job_events_jsonl_published_to_events_pubsub(tmp_path: Path) -> None:
    """Stage 12.5 (A5): when jobs/<slug>/events.jsonl is MODIFIED, the watcher
    reads new bytes and publishes typed Event records to the events_pubsub on
    both 'global' and 'job:<slug>' scopes.
    """
    cache = await Cache.bootstrap(tmp_path)
    change_bus: InProcessPubSub[CacheChange] = InProcessPubSub()
    events_bus: InProcessPubSub[Event] = InProcessPubSub()

    evpath = paths.job_events_jsonl("test-job", root=tmp_path)
    evpath.parent.mkdir(parents=True, exist_ok=True)
    evpath.write_text(_make_event_line(1, "test-job") + "\n")

    sub_global = events_bus.subscribe("global")
    sub_job = events_bus.subscribe("job:test-job")

    batch = {(Change.modified, str(evpath))}
    await run(cache, change_bus, events_bus, _watcher=_fake_stream([batch]))

    ev_g = await asyncio.wait_for(anext(sub_global), timeout=1.0)
    ev_j = await asyncio.wait_for(anext(sub_job), timeout=1.0)
    assert isinstance(ev_g, Event)
    assert ev_g.seq == 1
    assert ev_j.seq == 1
    # Cache is NOT updated — events.jsonl is not a state file
    assert cache.get_job("test-job") is None


@pytest.mark.asyncio
async def test_tailer_stage_events_jsonl_published_to_stage_scope(tmp_path: Path) -> None:
    """Stage 12.5 (A5): stage-level events.jsonl publishes to
    'global', 'job:<slug>', and 'stage:<slug>:<sid>' scopes.
    """
    cache = await Cache.bootstrap(tmp_path)
    change_bus: InProcessPubSub[CacheChange] = InProcessPubSub()
    events_bus: InProcessPubSub[Event] = InProcessPubSub()

    evpath = paths.stage_events_jsonl("test-job", "design", root=tmp_path)
    evpath.parent.mkdir(parents=True, exist_ok=True)
    evpath.write_text(_make_event_line(5, "test-job", stage_id="design") + "\n")

    sub_stage = events_bus.subscribe("stage:test-job:design")
    sub_job = events_bus.subscribe("job:test-job")
    sub_other = events_bus.subscribe("stage:test-job:other")

    batch = {(Change.modified, str(evpath))}
    await run(cache, change_bus, events_bus, _watcher=_fake_stream([batch]))

    ev_s = await asyncio.wait_for(anext(sub_stage), timeout=1.0)
    ev_j = await asyncio.wait_for(anext(sub_job), timeout=1.0)
    assert ev_s.seq == 5
    assert ev_j.seq == 5
    # Other stage scope must not receive anything
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(sub_other), timeout=0.1)


@pytest.mark.asyncio
async def test_tailer_events_jsonl_tails_only_new_bytes(tmp_path: Path) -> None:
    """Stage 12.5 (A5): second MODIFIED event for same file publishes only new
    events, not the ones already consumed (byte-offset tracking).
    """
    cache = await Cache.bootstrap(tmp_path)
    change_bus: InProcessPubSub[CacheChange] = InProcessPubSub()
    events_bus: InProcessPubSub[Event] = InProcessPubSub()

    evpath = paths.job_events_jsonl("test-job", root=tmp_path)
    evpath.parent.mkdir(parents=True, exist_ok=True)

    # First batch: write seq=1 and process
    line1 = _make_event_line(1, "test-job") + "\n"
    evpath.write_text(line1)
    batch1 = {(Change.modified, str(evpath))}

    # Second batch: append seq=2 and process
    line2 = _make_event_line(2, "test-job") + "\n"

    sub = events_bus.subscribe("global")

    async def _stream_with_append() -> AsyncIterator[set[tuple[Change, str]]]:
        yield batch1
        # Now append the second event
        with evpath.open("a") as f:
            f.write(line2)
        yield {(Change.modified, str(evpath))}

    await run(cache, change_bus, events_bus, _watcher=_stream_with_append())

    ev1 = await asyncio.wait_for(anext(sub), timeout=1.0)
    ev2 = await asyncio.wait_for(anext(sub), timeout=1.0)
    assert ev1.seq == 1
    assert ev2.seq == 2  # Only new bytes — no duplicate for seq=1
    # No more events
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(anext(sub), timeout=0.1)

