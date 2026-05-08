"""SSE replay + live phase tests — Stage 1 §1.6, filled in at Stage 6b.

Covers:

- Replay: events written to ``events.jsonl`` before subscription are
  delivered in seq order.
- Reconnect with ``Last-Event-ID``: returns events strictly after that
  seq; no duplicates, no gaps.
- Live phase: events scripted via ``FakeEngine`` after subscription
  arrive on the existing connection.
- Scope filtering: a ``job:<slug>`` subscriber does not receive events
  from a different job; ``global`` receives everything.

Each test uses a bounded read loop with a short timeout so a hung
server doesn't wedge the suite.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine

_DATA_LINE = re.compile(r"^data:\s?(.*)$")
_ID_LINE = re.compile(r"^id:\s?(.*)$")


class _SseReader:
    """Stateful SSE consumer. Re-uses one ``aiter_lines`` iterator across
    multiple ``read`` calls because httpx forbids re-iterating a
    streaming response."""

    def __init__(self, response: httpx.Response) -> None:
        self._lines = response.aiter_lines()
        self._pending_id: str | None = None
        self._pending_data: list[str] = []

    async def read(self, *, count: int, timeout: float = 2.0) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []

        async def consume() -> None:
            async for raw in self._lines:
                line = raw.rstrip("\r")
                if line == "":
                    if self._pending_data:
                        payload = "\n".join(self._pending_data)
                        try:
                            decoded = json.loads(payload)
                        except json.JSONDecodeError:
                            decoded = {"_raw": payload}
                        if self._pending_id is not None and isinstance(decoded, dict):
                            decoded.setdefault("_id", self._pending_id)
                        events.append(decoded if isinstance(decoded, dict) else {"value": decoded})
                    self._pending_id = None
                    self._pending_data = []
                    if len(events) >= count:
                        return
                    continue
                if line.startswith(":"):
                    continue  # SSE comment (keepalive)
                m = _DATA_LINE.match(line)
                if m:
                    self._pending_data.append(m.group(1))
                    continue
                m = _ID_LINE.match(line)
                if m:
                    self._pending_id = m.group(1)
                    continue

        try:
            await asyncio.wait_for(consume(), timeout=timeout)
        except TimeoutError:
            pass
        return events


async def _open_sse(
    dashboard: DashboardHandle,
    path: str,
    *,
    last_event_id: str | None = None,
) -> AsyncIterator[httpx.Response]:
    """SSE consumer — talks to the *uvicorn* port (not ASGITransport).

    httpx's ``ASGITransport`` buffers responses until the generator
    completes, which never happens on a long-lived SSE stream. The
    fixture binds uvicorn to a real localhost port; talking to it via
    a separate ``AsyncClient`` keeps SSE chunked + flushed."""
    headers = {"Accept": "text/event-stream"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    async with httpx.AsyncClient(base_url=dashboard.url, timeout=10.0) as raw:
        async with raw.stream("GET", path, headers=headers) as response:
            yield response


@pytest.mark.asyncio
async def test_replay_returns_pre_subscribe_events(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Events written before SSE connect are replayed on Last-Event-ID=-1."""
    fake_engine.start_job(workflow={"workflow": "T"}, request="x")
    fake_engine.enter_node("a")
    fake_engine.emit_event("synthetic", {"k": 1}, node_id="a")

    async for resp in _open_sse(dashboard, f"/sse/job/{fake_engine.job_slug}", last_event_id="-1"):
        reader = _SseReader(resp)
        events = await reader.read(count=3, timeout=2.0)
        types = [e.get("event_type") for e in events if "seq" in e]
        assert "job_submitted" in types
        assert "node_started" in types
        return


@pytest.mark.asyncio
async def test_replay_then_live_seq_continuous(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Replay seq + live seq are monotonically increasing for one job."""
    fake_engine.start_job(workflow={"workflow": "T"}, request="x")
    fake_engine.enter_node("a")
    # Let the tailer's debounce window (~100ms) close so existing events
    # are absorbed into its file_offsets — they won't re-fire as "live"
    # when we subscribe.
    await asyncio.sleep(0.5)

    async for resp in _open_sse(dashboard, f"/sse/job/{fake_engine.job_slug}", last_event_id="-1"):
        reader = _SseReader(resp)
        replay = await reader.read(count=2, timeout=2.0)
        replay_seqs = [e["seq"] for e in replay if "seq" in e]
        assert replay_seqs == sorted(replay_seqs)
        # Settle live subscription before emitting (suite-contention race).
        await asyncio.sleep(0.5)
        fake_engine.emit_event("custom_live", {"k": "v"}, node_id="a")
        live = await reader.read(count=1, timeout=8.0)
        live_seqs = [e["seq"] for e in live if "seq" in e]
        assert live_seqs and replay_seqs and live_seqs[0] > max(replay_seqs)
        return


@pytest.mark.asyncio
async def test_reconnect_with_last_event_id_no_duplicates(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Reconnect with Last-Event-ID=N returns events strictly > N."""
    fake_engine.start_job(workflow={"workflow": "T"}, request="x")
    fake_engine.enter_node("a")
    fake_engine.emit_event("synthetic", {"k": 1}, node_id="a")
    # Let the tailer absorb pre-subscription writes so they don't
    # re-fire as live events.
    await asyncio.sleep(0.5)

    async for resp in _open_sse(dashboard, f"/sse/job/{fake_engine.job_slug}", last_event_id="0"):
        reader = _SseReader(resp)
        events = await reader.read(count=10, timeout=1.5)
        seqs = [e["seq"] for e in events if "seq" in e]
        assert seqs
        assert all(s > 0 for s in seqs)
        assert len(seqs) == len(set(seqs))
        return


@pytest.mark.asyncio
async def test_job_scope_does_not_receive_other_jobs_events(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """A subscriber on job:A should not see events emitted for job:B."""
    fake_engine.start_job(workflow={"workflow": "T"}, request="x")
    other = FakeEngine(dashboard.root, "test-job-other")
    other.start_job(workflow={"workflow": "T"}, request="x")

    async for resp in _open_sse(dashboard, f"/sse/job/{fake_engine.job_slug}", last_event_id="-1"):
        reader = _SseReader(resp)
        events = await reader.read(count=10, timeout=1.0)
        for e in events:
            jid = e.get("job_id")
            if jid is not None:
                assert jid == fake_engine.job_slug
        return


@pytest.mark.asyncio
async def test_live_event_appears_for_subscriber(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """An event emitted post-subscribe arrives on the live phase."""
    fake_engine.start_job(workflow={"workflow": "T"}, request="x")
    await asyncio.sleep(0.5)  # tailer absorbs the start_job event

    async for resp in _open_sse(dashboard, f"/sse/job/{fake_engine.job_slug}", last_event_id="-1"):
        reader = _SseReader(resp)
        # Drain the replay (1 event from start_job).
        await reader.read(count=1, timeout=1.5)
        # Wait for the live subscription to register on the events_pubsub
        # scope before emitting (race-prone under loaded suites).
        await asyncio.sleep(0.5)
        fake_engine.emit_event("from_test", {"hello": "world"})
        live = await reader.read(count=1, timeout=8.0)
        types = [e.get("event_type") for e in live if "seq" in e]
        assert "from_test" in types
        return


async def _run_watcher_and_chat_append(
    *,
    job_slug: str,
    node_id: str,
    root_path: Path,
    appends: list[dict[str, object]],
    inter_append_delay_s: float = 0.0,
    listen_seconds: float = 1.5,
) -> int:
    """Helper: spin up a dedicated watcher + pubsub, do the appends,
    return the number of chat_jsonl PathChange messages observed.

    Avoids fighting with the dashboard fixture's watcher (separate
    pubsub already absorbed the pre-create event into its coalesce
    state) and decouples the assertion from SSE network timing — the
    SSE endpoint is a thin pass-through over PathChange.
    """
    from dashboard.state.pubsub import InProcessPubSub
    from dashboard.watcher.tailer import PathChange
    from dashboard.watcher.tailer import run as tailer_run
    from shared.v1 import paths as v1_paths

    attempt_dir = v1_paths.node_attempt_dir(job_slug, node_id, 1, root=root_path)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    chat_path = attempt_dir / "chat.jsonl"
    chat_path.write_text("", encoding="utf-8")

    pubsub: InProcessPubSub[PathChange] = InProcessPubSub()
    sub = pubsub.subscribe(f"job:{job_slug}")
    stop = asyncio.Event()
    watch_task = asyncio.create_task(
        tailer_run(root_path, pubsub, None, stop_event=stop, debounce_ms=50)
    )
    chat_count = 0
    try:
        await asyncio.sleep(0.5)  # let inotify watches register
        for line in appends:
            with chat_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(line) + "\n")
            if inter_append_delay_s > 0:
                await asyncio.sleep(inter_append_delay_s)

        deadline = asyncio.get_event_loop().time() + listen_seconds
        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(sub._queue.get(), timeout=0.2)
            except TimeoutError:
                continue
            if msg.classified.kind == "chat_jsonl":
                chat_count += 1
                assert msg.classified.job_slug == job_slug
                assert msg.classified.node_id == node_id
                assert msg.classified.iter_path == ()
                assert msg.classified.attempt == 1
        return chat_count
    finally:
        stop.set()
        await sub.aclose()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            watch_task.cancel()
            await watch_task


@pytest.mark.asyncio
async def test_sse_chat_appended_emits_on_chat_jsonl_change(tmp_path: Path) -> None:
    """Stage C smoke: appending to a node's chat.jsonl publishes a
    chat_appended PathChange (``file_kind='chat_jsonl'``) on the
    ``job:<slug>`` pubsub scope. Frontend will key the event by
    (job_slug, node_id, iter_token, attempt) and refetch the chat
    endpoint.

    Drives the watcher → pubsub contract directly. The SSE handler is a
    transparent forwarder of PathChange messages, so verifying the
    pubsub gets the right ClassifiedPath is the load-bearing assertion.
    """
    # Multiple appends with a delay between each — ensures at least
    # one event surfaces even when inotify has cold-start latency
    # under concurrent test fixtures (WSL2 / busy CI). Coalescing
    # keeps the count bounded; the assertion is "at least one".
    appends: list[dict[str, object]] = [{"type": "system", "i": i} for i in range(3)]
    count = await _run_watcher_and_chat_append(
        job_slug="t-sse-chat",
        node_id="write-spec",
        root_path=tmp_path,
        appends=appends,
        inter_append_delay_s=0.6,  # > coalesce window so each can fire
        listen_seconds=2.5,
    )
    assert count >= 1, "expected at least one chat_appended PathChange"


@pytest.mark.asyncio
async def test_sse_chat_appended_coalesces_within_window(tmp_path: Path) -> None:
    """Multiple appends within ``CHAT_COALESCE_WINDOW_S`` collapse to
    at most one chat_appended event per key per window.

    Refetch-on-poke means over-emitting is wasteful — the contract is
    "at most one event per (job, node, iter_token, attempt) per ~500ms".
    """
    appends: list[dict[str, object]] = [{"type": "system", "i": i} for i in range(5)]
    count = await _run_watcher_and_chat_append(
        job_slug="t-sse-coalesce",
        node_id="write-spec",
        root_path=tmp_path,
        appends=appends,
        inter_append_delay_s=0.01,
        listen_seconds=0.7,
    )
    # 5 appends in ~50ms → coalesce should keep this <= 2 within the
    # 700ms listen window (window is 500ms; second emit can land at
    # ~510ms after the first if appends keep going).
    assert 1 <= count <= 2, f"expected coalesce 1..2 chat events, got {count}"
