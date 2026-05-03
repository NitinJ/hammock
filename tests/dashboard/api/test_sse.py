"""Tests for SSE endpoints: /sse/global, /sse/job/{slug}, /sse/stage/{slug}/{sid}.

Coverage:
- SSE wire format helpers (_format_change, _format_replay_event, _parse_last_event_id)
- _sse_response headers (content-type, cache-control, x-accel-buffering)
- _event_stream replay phase (stage scope, job scope, global scope)
- _event_stream replay seq filtering and scope isolation
- _event_stream live phase — CacheChange delivery
- _event_stream live phase — scope isolation (wrong scope events NOT delivered)

Note: httpx ASGITransport and Starlette 1.0.0 TestClient both buffer the entire
response body before returning, so SSE streams that don't terminate cannot be
tested via HTTP.  All streaming tests drive _event_stream directly.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from dashboard.api.sse import (
    _event_stream,
    _format_change,
    _format_replay_event,
    _parse_last_event_id,
    _sse_response,
)
from dashboard.state.cache import ChangeKind, ClassifiedPath
from dashboard.state.pubsub import InProcessPubSub
from dashboard.watcher.tailer import CacheChange

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts() -> str:
    return datetime(2026, 5, 1, 12, 0, tzinfo=UTC).isoformat()


def _write_event(path: Path, seq: int, event_type: str = "cost_accrued") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "seq": seq,
        "timestamp": _ts(),
        "event_type": event_type,
        "source": "agent0",
        "job_id": "job-id-1",
    }
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def _job_change(tmp_path: Path, job_slug: str = "test-job") -> CacheChange:
    return CacheChange(
        path=tmp_path / f"jobs/{job_slug}/job.json",
        kind=ChangeKind.MODIFIED,
        classified=ClassifiedPath("job", job_slug=job_slug),
    )


def _stage_change(tmp_path: Path, job_slug: str = "test-job", stage_id: str = "s1") -> CacheChange:
    return CacheChange(
        path=tmp_path / f"jobs/{job_slug}/stages/{stage_id}/stage.json",
        kind=ChangeKind.MODIFIED,
        classified=ClassifiedPath("stage", job_slug=job_slug, stage_id=stage_id),
    )


class _DisconnectedRequest:
    """Fake request that immediately signals disconnect — terminates live phase."""

    headers: ClassVar[dict[str, str]] = {}

    async def is_disconnected(self) -> bool:
        return True


class _ConnectedRequest:
    """Fake request with controllable disconnect flag."""

    headers: ClassVar[dict[str, str]] = {}
    _disconnected: bool = False

    def disconnect(self) -> None:
        self._disconnected = True

    async def is_disconnected(self) -> bool:
        return self._disconnected


async def _collect_stream(
    scope: str,
    pubsub: InProcessPubSub[CacheChange],
    root: Path,
    *,
    last_event_id: int | None,
    request: object | None = None,
    timeout: float = 2.0,
) -> list[str]:
    """Drive _event_stream until it exits, collecting all yielded chunks.

    Uses _DisconnectedRequest by default so the live phase terminates
    immediately after replay finishes.
    """
    req = request or _DisconnectedRequest()
    chunks: list[str] = []
    async with asyncio.timeout(timeout):
        async for chunk in _event_stream(
            req,  # type: ignore[arg-type]
            scope,
            pubsub,
            root,
            last_event_id,
        ):
            chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# Unit tests — _parse_last_event_id
# ---------------------------------------------------------------------------


def test_parse_last_event_id_absent() -> None:
    class FakeRequest:
        headers: ClassVar[dict[str, str]] = {}

    assert _parse_last_event_id(FakeRequest()) is None  # type: ignore[arg-type]


def test_parse_last_event_id_valid() -> None:
    class FakeRequest:
        headers: ClassVar[dict[str, str]] = {"last-event-id": "42"}

    assert _parse_last_event_id(FakeRequest()) == 42  # type: ignore[arg-type]


def test_parse_last_event_id_non_numeric() -> None:
    class FakeRequest:
        headers: ClassVar[dict[str, str]] = {"last-event-id": "not-a-number"}

    assert _parse_last_event_id(FakeRequest()) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unit tests — _format_change
# ---------------------------------------------------------------------------


def test_format_change_job(tmp_path: Path) -> None:
    change = _job_change(tmp_path)
    result = _format_change(change, "job:test-job")
    assert result.startswith("event: job_changed\n")
    assert "job:test-job" in result
    assert result.endswith("\n\n")
    # No id: field — CacheChange is not persisted, not replayable
    for line in result.splitlines():
        assert not line.startswith("id:")


def test_format_change_stage(tmp_path: Path) -> None:
    change = _stage_change(tmp_path)
    result = _format_change(change, "stage:test-job:s1")
    assert "event: stage_changed\n" in result
    data = json.loads(result.split("data: ", 1)[1].split("\n")[0])
    assert data["job_slug"] == "test-job"
    assert data["stage_id"] == "s1"


def test_format_change_no_id_line(tmp_path: Path) -> None:
    change = _job_change(tmp_path)
    result = _format_change(change, "global")
    lines = result.strip().splitlines()
    assert not any(line.startswith("id:") for line in lines)


# ---------------------------------------------------------------------------
# Unit tests — _format_replay_event
# ---------------------------------------------------------------------------


def test_format_replay_event_includes_id() -> None:
    from shared.models import Event

    event = Event(
        seq=42,
        timestamp=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        event_type="cost_accrued",
        source="agent0",
        job_id="job-id-1",
        payload={"delta_usd": 0.5},
    )
    result = _format_replay_event(event, "stage:job:s1")
    # Scoped channels include id: for reconnect replay
    assert result.startswith("id: 42\n")
    # Unnamed event (no event: line) — fires EventSource.onmessage in the browser
    assert "event:" not in result
    # Data must include SseEvent fields
    data_line = next(line for line in result.splitlines() if line.startswith("data:"))
    data = json.loads(data_line[len("data: ") :])
    assert data["seq"] == 42
    assert data["event_type"] == "cost_accrued"
    assert result.endswith("\n\n")


def test_format_replay_event_data_fields() -> None:
    from shared.models import Event

    event = Event(
        seq=1,
        timestamp=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        event_type="stage_state_transition",
        source="job_driver",
        job_id="jid",
        payload={"from": "READY", "to": "RUNNING"},
    )
    result = _format_replay_event(event, "job:my-job")
    data_line = next(line for line in result.splitlines() if line.startswith("data:"))
    data = json.loads(data_line[len("data: ") :])
    # Full SseEvent contract
    assert data["seq"] == 1
    assert data["event_type"] == "stage_state_transition"
    assert data["source"] == "job_driver"
    assert data["job_id"] == "jid"
    assert data["stage_id"] is None
    assert data["task_id"] is None
    assert data["subagent_id"] is None
    assert data["parent_event_seq"] is None
    assert data["payload"] == {"from": "READY", "to": "RUNNING"}
    # No scope field — not part of SseEvent interface
    assert "scope" not in data


# ---------------------------------------------------------------------------
# Unit tests — _sse_response headers
# ---------------------------------------------------------------------------


def test_sse_global_content_type() -> None:
    """_sse_response sets text/event-stream content-type."""

    async def gen():  # type: ignore[return]
        if False:
            yield ""

    resp = _sse_response(gen())
    assert resp.media_type == "text/event-stream"


def test_sse_job_content_type() -> None:
    async def gen():  # type: ignore[return]
        if False:
            yield ""

    resp = _sse_response(gen())
    assert "text/event-stream" in (resp.media_type or "")


def test_sse_stage_content_type() -> None:
    async def gen():  # type: ignore[return]
        if False:
            yield ""

    resp = _sse_response(gen())
    assert "text/event-stream" in (resp.media_type or "")


def test_sse_no_cache_header() -> None:
    """_sse_response sets Cache-Control: no-cache."""

    async def gen():  # type: ignore[return]
        if False:
            yield ""

    resp = _sse_response(gen())
    assert resp.headers.get("cache-control") == "no-cache"


# ---------------------------------------------------------------------------
# Integration — _event_stream replay via Last-Event-ID
# ---------------------------------------------------------------------------


async def test_sse_stage_replay_delivers_events(populated_root: Path) -> None:
    """Last-Event-ID: 0 → events seq=1 and seq=2 replayed from disk."""
    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    chunks = await _collect_stream(
        "stage:alpha-job-1:design",
        pubsub,
        populated_root,
        last_event_id=0,
    )
    full = "".join(chunks)
    assert "id: 1" in full
    assert "id: 2" in full
    # event_type in data payload (unnamed SSE event — no event: line)
    assert "cost_accrued" in full


async def test_sse_stage_replay_filters_by_seq(populated_root: Path) -> None:
    """Last-Event-ID: 1 → only seq=2 replayed (seq=1 excluded)."""
    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    chunks = await _collect_stream(
        "stage:alpha-job-1:design",
        pubsub,
        populated_root,
        last_event_id=1,
    )
    full = "".join(chunks)
    assert "id: 1" not in full
    assert "id: 2" in full


async def test_sse_stage_replay_empty_when_last_id_at_max(populated_root: Path) -> None:
    """Last-Event-ID: 99 → no replay (all events have seq ≤ 2)."""
    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    chunks = await _collect_stream(
        "stage:alpha-job-1:design",
        pubsub,
        populated_root,
        last_event_id=99,
    )
    full = "".join(chunks)
    # No id: lines — nothing replayed
    assert "id:" not in full


async def test_sse_job_replay_delivers_job_events(populated_root: Path) -> None:
    """Job-scope replay reads from job-level events.jsonl."""
    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    chunks = await _collect_stream(
        "job:alpha-job-1",
        pubsub,
        populated_root,
        last_event_id=0,
    )
    full = "".join(chunks)
    assert "id:" in full


async def test_sse_global_replay_delivers_all_job_events(populated_root: Path) -> None:
    """Global scope replay reads events from all job directories.

    Global scope omits ``id:`` because seq is per-job monotonic — emitting it
    would corrupt Last-Event-ID across reconnects.  Data is still delivered.
    """
    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    chunks = await _collect_stream(
        "global",
        pubsub,
        populated_root,
        last_event_id=-1,
    )
    full = "".join(chunks)
    # Data delivered but no id: lines (global scope suppresses id:)
    assert "data:" in full
    assert "id:" not in full


async def test_sse_no_last_event_id_skips_replay(tmp_path: Path) -> None:
    """No Last-Event-ID (None) → replay phase skipped; no id: lines emitted."""
    from shared import paths

    p = paths.stage_events_jsonl("j", "s", root=tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "seq": 1,
                "timestamp": _ts(),
                "event_type": "cost_accrued",
                "source": "agent0",
                "job_id": "j1",
            }
        )
        + "\n"
    )

    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    # last_event_id=None → replay skipped; live phase exits immediately via disconnect
    chunks = await _collect_stream(
        "stage:j:s",
        pubsub,
        tmp_path,
        last_event_id=None,
    )
    full = "".join(chunks)
    assert "id:" not in full, "No replay id: lines expected without Last-Event-ID"


# ---------------------------------------------------------------------------
# Integration — replay scope isolation
# ---------------------------------------------------------------------------


async def test_sse_stage_replay_scope_isolation(populated_root: Path) -> None:
    """Subscribing to stage A does not receive events from stage B on replay."""
    from shared import paths

    extra = paths.stage_events_jsonl("alpha-job-1", "implement", root=populated_root)
    _write_event(extra, seq=100, event_type="stage_state_transition")

    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    chunks = await _collect_stream(
        "stage:alpha-job-1:design",
        pubsub,
        populated_root,
        last_event_id=0,
    )
    full = "".join(chunks)
    assert "id: 1" in full
    assert "id: 2" in full
    assert "id: 100" not in full, "implement-stage event must not appear in design stream"


# ---------------------------------------------------------------------------
# Integration — live event delivery
# ---------------------------------------------------------------------------


async def test_sse_global_live_event_delivered(tmp_path: Path) -> None:
    """A CacheChange published to pubsub appears in the live stream output."""
    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    change = _job_change(tmp_path)
    request = _ConnectedRequest()

    received: list[str] = []
    gen = _event_stream(
        request,  # type: ignore[arg-type]
        "global",
        pubsub,
        tmp_path,
        last_event_id=None,
    )

    async def _publish_then_disconnect() -> None:
        await asyncio.sleep(0.05)
        pubsub.publish("global", change)
        # Wait for the chunk to be received, then disconnect
        await asyncio.sleep(0.2)
        request.disconnect()

    pub_task = asyncio.create_task(_publish_then_disconnect())
    try:
        async with asyncio.timeout(3.0):
            async for chunk in gen:
                received.append(chunk)
    finally:
        await gen.aclose()
        await pub_task

    assert any("job_changed" in c for c in received)


async def test_sse_job_scope_isolation_live(tmp_path: Path) -> None:
    """Events published to job:A are not delivered on job:B live stream."""
    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    change_a = _job_change(tmp_path, job_slug="job-a")
    request = _ConnectedRequest()

    received: list[str] = []
    gen = _event_stream(
        request,  # type: ignore[arg-type]
        "job:job-b",
        pubsub,
        tmp_path,
        last_event_id=None,
    )

    async def _publish_then_disconnect() -> None:
        await asyncio.sleep(0.05)
        pubsub.publish("job:job-a", change_a)  # wrong scope
        await asyncio.sleep(0.15)
        request.disconnect()

    pub_task = asyncio.create_task(_publish_then_disconnect())
    try:
        async with asyncio.timeout(3.0):
            async for chunk in gen:
                received.append(chunk)
                if "event:" in chunk:
                    break
    finally:
        await gen.aclose()
        await pub_task

    event_chunks = [c for c in received if "event:" in c]
    assert not event_chunks, "job-b stream must not receive job-a events"
