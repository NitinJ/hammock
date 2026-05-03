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
from shared.models import Event

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
    events_pubsub: InProcessPubSub[Event] | None = None,
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
            events_pubsub,
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
    """Stage 12.5 (A4): live CacheChange must be an *unnamed* SSE event.

    Named events (``event: job_changed\\n``) only fire ``addEventListener``
    listeners; the frontend uses ``source.onmessage`` which only fires for
    unnamed events.  After the A4 fix, ``_format_change`` must NOT emit an
    ``event:`` line — the browser fires ``onmessage`` and the consumer narrows
    on ``change_kind`` to distinguish live events from replay events.
    """
    change = _job_change(tmp_path)
    result = _format_change(change, "job:test-job")
    # Must be an unnamed event — no event: line
    lines = result.splitlines()
    assert not any(line.startswith("event:") for line in lines), (
        "live CacheChange must NOT have an event: line (A4 fix)"
    )
    # Must include scope in data payload
    assert "job:test-job" in result
    assert result.endswith("\n\n")
    # No id: field — CacheChange is not persisted, not replayable
    assert not any(line.startswith("id:") for line in lines)
    # change_kind must appear in the data payload so consumers can narrow
    data_line = next(line for line in lines if line.startswith("data:"))
    data = json.loads(data_line[len("data: ") :])
    assert data["change_kind"] == "modified"
    assert data["file_kind"] == "job"


def test_format_change_stage(tmp_path: Path) -> None:
    change = _stage_change(tmp_path)
    result = _format_change(change, "stage:test-job:s1")
    # Must be unnamed — no event: line (A4 fix)
    lines = result.splitlines()
    assert not any(line.startswith("event:") for line in lines), (
        "live CacheChange must NOT have an event: line"
    )
    data = json.loads(result.split("data: ", 1)[1].split("\n")[0])
    assert data["job_slug"] == "test-job"
    assert data["stage_id"] == "s1"
    assert data["change_kind"] == "modified"


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


async def test_sse_global_replay_with_high_last_event_id_does_not_drop_low_seq_jobs(
    populated_root: Path,
) -> None:
    """Stage 12.5 (A8): on global scope, Last-Event-ID must not filter per-job
    events — seq is per-job monotonic, not globally monotonic, so applying a
    high Last-Event-ID as a per-file ``seq > N`` filter would silently drop
    every event from any job whose local seq is below N.  Pre-12.5 this was
    exactly the bug: a client that sent ``Last-Event-ID: 100`` (perhaps from
    confusion with another scope) would receive nothing from a job whose seq
    only went up to 3.

    Fix: global scope ignores any Last-Event-ID and replays every event from
    every job's events.jsonl.  The fixture has events with seq 1..3 across
    multiple jobs; passing Last-Event-ID:100 must still deliver them.
    """
    # Augment the fixture with a SECOND job that also has a low-seq event,
    # so we prove cross-job behaviour: a high Last-Event-ID must not drop
    # events from the second job either.  Without this, the test would
    # only cover one job and could pass for the wrong reason.
    from shared import paths

    second_events = paths.job_events_jsonl("alpha-job-2", root=populated_root)
    second_events.parent.mkdir(parents=True, exist_ok=True)
    second_events.write_text(
        json.dumps(
            {
                "seq": 1,
                "timestamp": _ts(),
                "event_type": "cost_accrued",
                "source": "agent0",
                "job_id": "id-alpha-job-2",
                "stage_id": "second-job-stage",
                "payload": {"delta_usd": 0.99, "delta_tokens": 7},
            }
        )
        + "\n"
    )

    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    chunks = await _collect_stream(
        "global",
        pubsub,
        populated_root,
        last_event_id=100,
    )
    full = "".join(chunks)
    # alpha-job-1 has seq 1..4; alpha-job-2 has seq 1.  All must be delivered
    # despite Last-Event-ID:100 — the per-job seq filter must not drop low-seq
    # events from any job.
    assert "cost_accrued" in full, (
        "global replay must deliver low-seq events even when Last-Event-ID is high"
    )
    # Both jobs' events must appear — proves cross-job behaviour, not just
    # that one job's events leaked through.
    assert "second-job-stage" in full, "second job's low-seq event was silently dropped"
    # Global suppresses id: regardless
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

    # After A4 fix: live events are unnamed; check for change_kind in data payload
    assert any("job" in c and "change_kind" in c for c in received)


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


# ---------------------------------------------------------------------------
# Stage 12.5 (A5) — live typed Event delivery via events_pubsub
# ---------------------------------------------------------------------------


async def test_sse_live_typed_event_delivered_via_events_pubsub(tmp_path: Path) -> None:
    """Stage 12.5 (A5): a typed Event published to events_pubsub appears in
    the SSE live stream with ``id: <seq>`` so the browser updates Last-Event-ID.

    This covers the second channel in _event_stream — typed Event records from
    events.jsonl tail delivery, distinct from CacheChange (state-file mutations).
    """
    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    events_pubsub: InProcessPubSub[Event] = InProcessPubSub()
    request = _ConnectedRequest()

    live_event = Event(
        seq=7,
        timestamp=datetime(2026, 5, 2, 12, 0, tzinfo=UTC),
        event_type="cost_accrued",
        source="agent0",
        job_id="job-id-live",
        payload={"delta_usd": 0.07},
    )

    received: list[str] = []
    gen = _event_stream(
        request,  # type: ignore[arg-type]
        "job:test-job",
        pubsub,
        tmp_path,
        None,  # last_event_id — skip replay
        events_pubsub,
    )

    async def _publish_then_disconnect() -> None:
        await asyncio.sleep(0.05)
        events_pubsub.publish("job:test-job", live_event)
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

    full = "".join(received)
    # Typed Event must arrive with id: for reconnect tracking
    assert "id: 7" in full, "live typed event must include id: <seq>"
    assert "cost_accrued" in full
    assert "delta_usd" in full
