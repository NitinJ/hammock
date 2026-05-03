"""In-process scoped pub/sub + on-disk replay.

Subscribers consume an :class:`PubSubSubscription` (an async iterator) for a
named scope; publishers fan out to all live subscribers on that scope.

Per design doc § Real-time delivery and § Process structure: scoped pub/sub
is the bridge between the watcher (writes) and the SSE handlers (reads).
Stage 1 ships the mechanism; Stage 10 adds :func:`replay_since` for
Last-Event-ID reconnect replay from on-disk JSONL files.

Generic over the message type — Stage 1 uses :class:`CacheChange`, future
stages use ``shared.models.Event`` (e.g. for tailing ``events.jsonl``).
Type-parametric so we don't have to coerce everything to one envelope.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from pathlib import Path
from weakref import WeakSet

from shared.models import Event


class PubSubSubscription[T]:
    """Async-iterator handle backed by an :class:`asyncio.Queue`.

    Cleans up its registration when the iterator is closed (via ``aclose``)
    or garbage-collected.
    """

    def __init__(
        self,
        scope: str,
        queue: asyncio.Queue[T],
        unregister: Callable[[PubSubSubscription[T]], None],
    ) -> None:
        self.scope = scope
        self._queue = queue
        self._unregister = unregister
        self._closed = False

    def __aiter__(self) -> AsyncIterator[T]:
        return self

    async def __anext__(self) -> T:
        if self._closed:
            raise StopAsyncIteration
        return await self._queue.get()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._unregister(self)

    def deliver(self, message: T) -> None:
        """Internal: publisher-side push. Used by :class:`InProcessPubSub`."""
        self._queue.put_nowait(message)


class InProcessPubSub[T]:
    """Scope-keyed message bus, single-process, asyncio-native.

    Implementation notes:

    - One :class:`asyncio.Queue` per subscription. Slow subscribers don't
      block fast ones — they just back up their own queue.
    - Subscriptions are tracked per scope in a :class:`WeakSet` so dangling
      subscriptions don't pin memory if a caller drops the handle.
    - ``publish`` is synchronous: it puts onto each queue without awaiting.
      If a queue is full (queues are unbounded by default in v0) we'd lose
      messages — but unbounded queues mean we trade memory for never losing.
    """

    def __init__(self) -> None:
        self._scopes: dict[str, WeakSet[PubSubSubscription[T]]] = {}

    def subscribe(self, scope: str, *, maxsize: int = 0) -> PubSubSubscription[T]:
        """Open a new subscription on *scope*.

        ``maxsize`` defaults to 0 (unbounded). Pass a positive integer to
        cap the per-subscriber queue.
        """
        queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)
        subs = self._scopes.setdefault(scope, WeakSet())

        sub = PubSubSubscription(scope, queue, self._unregister)
        subs.add(sub)
        return sub

    def publish(self, scope: str, message: T) -> None:
        """Fan out *message* to every live subscriber on *scope*.

        Synchronous; uses ``put_nowait`` so a single full queue does not
        block other subscribers.
        """
        subs = self._scopes.get(scope)
        if not subs:
            return
        # WeakSet may release entries during iteration; snapshot first.
        # asyncio.QueueFull → drop (slow subscriber). v0 accepts this; a
        # production setup would surface this as an event.
        for sub in list(subs):
            with contextlib.suppress(asyncio.QueueFull):
                sub.deliver(message)

    def subscriber_count(self, scope: str) -> int:
        """Diagnostic — number of live subscribers on *scope*."""
        subs = self._scopes.get(scope)
        return len(subs) if subs is not None else 0

    def _unregister(self, sub: PubSubSubscription[T]) -> None:
        subs = self._scopes.get(sub.scope)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                self._scopes.pop(sub.scope, None)


# ---------------------------------------------------------------------------
# On-disk replay (Stage 10)
# ---------------------------------------------------------------------------


async def replay_since(
    scope: str,
    last_event_id: int,
    *,
    root: Path,
) -> AsyncGenerator[Event, None]:
    """Yield :class:`~shared.models.Event` objects from on-disk JSONL files.

    Reads the JSONL file(s) matching *scope*, yields every ``Event`` whose
    ``seq > last_event_id`` in file order.  Malformed or partially-written
    lines are silently skipped.

    Per design doc § Real-time delivery § Reconnect and replay: the SSE
    handler calls this on reconnect *before* joining the live pub/sub stream,
    so the client receives exactly the missed events with no gaps or
    duplicates.

    Scope strings:

    - ``"global"``  — all ``jobs/<slug>/events.jsonl`` files under *root*.
    - ``"job:<slug>"``  — ``jobs/<slug>/events.jsonl``.
    - ``"stage:<job>:<sid>"``  — ``jobs/<job>/stages/<sid>/events.jsonl``.
    """
    for jsonl_path in _jsonl_paths_for_scope(scope, root=root):
        if not jsonl_path.exists():
            continue
        for raw_line in jsonl_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                event = Event.model_validate(data)
            except Exception:
                continue
            if event.seq > last_event_id:
                yield event


def _jsonl_paths_for_scope(scope: str, *, root: Path) -> list[Path]:
    """Return the on-disk JSONL path(s) that back the given *scope*."""
    from shared import paths as _paths  # local import avoids dashboard→shared→dashboard cycle

    if scope == "global":
        jdir = _paths.jobs_dir(root=root)
        if not jdir.exists():
            return []
        return [
            _paths.job_events_jsonl(job_dir.name, root=root)
            for job_dir in sorted(jdir.iterdir())
            if job_dir.is_dir()
        ]
    if scope.startswith("job:"):
        return [_paths.job_events_jsonl(scope[4:], root=root)]
    if scope.startswith("stage:"):
        rest = scope[6:]
        sep = rest.find(":")
        if sep == -1:
            return []
        return [_paths.stage_events_jsonl(rest[:sep], rest[sep + 1 :], root=root)]
    return []
