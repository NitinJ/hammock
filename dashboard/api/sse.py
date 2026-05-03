"""SSE endpoints — /sse/global, /sse/job/{slug}, /sse/stage/{slug}/{sid}.

Per design doc § Real-time delivery: three scoped SSE channels with
Last-Event-ID replay from on-disk jsonl files and 15-second keepalives.

Stage 10 ships the mechanism; the frontend wires up EventSource in Stage 11.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from dashboard.state.pubsub import InProcessPubSub, replay_since
from dashboard.watcher.tailer import CacheChange

router = APIRouter(tags=["sse"])

KEEPALIVE_INTERVAL: float = 15.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_last_event_id(request: Request) -> int | None:
    """Return Last-Event-ID header as int, or None if absent / non-numeric."""
    raw = request.headers.get("last-event-id")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _format_change(change: CacheChange, scope: str) -> str:
    """Format a CacheChange as an SSE message.

    No ``id:`` field — CacheChange events are not persisted to disk, so they
    cannot be replayed. The browser does not update Last-Event-ID for lines
    without an ``id:`` field (per SSE spec).
    """
    classified = change.classified
    data: dict[str, object] = {
        "scope": scope,
        "change_kind": change.kind.value,
        "file_kind": classified.kind,
    }
    if classified.job_slug is not None:
        data["job_slug"] = classified.job_slug
    if classified.stage_id is not None:
        data["stage_id"] = classified.stage_id
    if classified.project_slug is not None:
        data["project_slug"] = classified.project_slug
    if classified.hil_id is not None:
        data["hil_id"] = classified.hil_id
    event_type = f"{classified.kind}_changed"
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _format_replay_event(event: object, scope: str) -> str:
    """Format a shared.models.Event (from JSONL replay) as an SSE message.

    Unnamed events (no ``event:`` line) fire ``EventSource.onmessage``, which
    is how the frontend wires up its handler.  Named events only reach
    ``addEventListener(type, handler)`` listeners — the frontend does not use
    those for log replay.

    Data payload matches the SseEvent TypeScript interface exactly so the
    frontend can parse the raw ``event.data`` without field mapping.

    ``id: <seq>`` is included for scoped channels (job, stage) so the browser
    updates Last-Event-ID, enabling reconnect replay.  Global scope omits
    ``id:`` because seq is per-job monotonic (not globally unique) — emitting
    it would corrupt Last-Event-ID across reconnects.
    """
    from shared.models import Event as _Event

    e: _Event = event  # type: ignore[assignment]
    data = {
        "seq": e.seq,
        "timestamp": e.timestamp.isoformat(),
        "event_type": e.event_type,
        "source": e.source,
        "job_id": e.job_id,
        "stage_id": e.stage_id,
        "task_id": e.task_id,
        "subagent_id": e.subagent_id,
        "parent_event_seq": e.parent_event_seq,
        "payload": e.payload,
    }
    # Global scope: suppress id: — seq is per-job, not globally monotonic.
    id_line = "" if scope == "global" else f"id: {e.seq}\n"
    return f"{id_line}data: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------


async def _poll_disconnected(request: Request) -> None:
    """Return as soon as the client disconnects (polls every 50 ms)."""
    while not await request.is_disconnected():
        await asyncio.sleep(0.05)


async def _event_stream(
    request: Request,
    scope: str,
    pubsub: InProcessPubSub[CacheChange],
    root: Path,
    last_event_id: int | None,
) -> AsyncGenerator[str, None]:
    """Replay from disk, then forward live pub/sub events with keepalives.

    Two phases:

    1. **Replay** — if the client sent ``Last-Event-ID: N``, yield all
       on-disk events for *scope* with ``seq > N``.  These carry ``id:``
       so the browser updates its last-seen seq.

    2. **Live** — subscribe to *pubsub* on *scope*.  Each :class:`CacheChange`
       is translated to an SSE message (no ``id:`` — not persistently
       replayable).  A ``": keepalive"`` comment is sent every
       :data:`KEEPALIVE_INTERVAL` seconds of inactivity to keep the
       connection alive through proxies.

    Disconnect detection races against both the message queue and the
    keepalive timer so the generator exits within ~50 ms of client close
    rather than waiting up to KEEPALIVE_INTERVAL seconds.

    Stage 12.5 (A8): on global scope the server never emits ``id:`` (seq is
    per-job, not globally monotonic), so any Last-Event-ID a client sends
    on global is meaningless.  Pre-12.5 we still applied that header as a
    per-job ``seq > N`` filter, which silently dropped every event from any
    job whose local seq was below N.  Now: on global, replay everything
    regardless (force ``last_event_id=-1``), so a client cannot lose data
    by misconfiguration.
    """
    # Phase 1 — replay
    if last_event_id is not None:
        replay_floor = -1 if scope == "global" else last_event_id
        async for event in replay_since(scope, replay_floor, root=root):
            yield _format_replay_event(event, scope)

    # Phase 2 — live stream
    # NOTE: The live pubsub carries CacheChange (state-file mutations), not
    # Event log entries from events.jsonl.  The watcher classifies events.jsonl
    # appends as "unknown" and does not publish them here.  Live delivery of
    # log events (tailing) is deferred to Stage 11.
    sub = pubsub.subscribe(scope)
    try:
        while True:
            msg_task = asyncio.create_task(sub._queue.get())  # type: ignore[attr-defined]
            ka_task = asyncio.create_task(asyncio.sleep(KEEPALIVE_INTERVAL))
            dc_task = asyncio.create_task(_poll_disconnected(request))

            done, _ = await asyncio.wait(
                {msg_task, ka_task, dc_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in (msg_task, ka_task, dc_task):
                if t not in done:
                    t.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await t

            if dc_task in done:
                break

            if msg_task in done:
                try:
                    change = msg_task.result()
                except Exception:
                    break
                yield _format_change(change, scope)
            else:
                yield ": keepalive\n\n"
    finally:
        await sub.aclose()


def _sse_response(generator: AsyncGenerator[str, None]) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx response buffering
        },
    )


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.get("/sse/global")
async def sse_global(request: Request) -> StreamingResponse:
    """Cross-job lifecycle + HIL events."""
    pubsub: InProcessPubSub[CacheChange] = request.app.state.pubsub  # type: ignore[attr-defined]
    root: Path = request.app.state.settings.root  # type: ignore[attr-defined]
    return _sse_response(
        _event_stream(request, "global", pubsub, root, _parse_last_event_id(request))
    )


@router.get("/sse/job/{job_slug}")
async def sse_job(request: Request, job_slug: str) -> StreamingResponse:
    """Job-scoped events — stage transitions, cost deltas, HIL opens."""
    pubsub: InProcessPubSub[CacheChange] = request.app.state.pubsub  # type: ignore[attr-defined]
    root: Path = request.app.state.settings.root  # type: ignore[attr-defined]
    return _sse_response(
        _event_stream(request, f"job:{job_slug}", pubsub, root, _parse_last_event_id(request))
    )


@router.get("/sse/stage/{job_slug}/{stage_id}")
async def sse_stage(request: Request, job_slug: str, stage_id: str) -> StreamingResponse:
    """Stage-scoped events — the high-volume stream fed by the Agent0 pane."""
    pubsub: InProcessPubSub[CacheChange] = request.app.state.pubsub  # type: ignore[attr-defined]
    root: Path = request.app.state.settings.root  # type: ignore[attr-defined]
    return _sse_response(
        _event_stream(
            request,
            f"stage:{job_slug}:{stage_id}",
            pubsub,
            root,
            _parse_last_event_id(request),
        )
    )
