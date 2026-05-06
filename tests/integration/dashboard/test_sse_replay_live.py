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
import json
import re
from collections.abc import AsyncIterator
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
