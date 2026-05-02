"""In-process scoped pub/sub.

Subscribers consume an :class:`PubSubSubscription` (an async iterator) for a
named scope; publishers fan out to all live subscribers on that scope.

Per design doc § Real-time delivery and § Process structure: scoped pub/sub
is the bridge between the watcher (writes) and the SSE handlers (reads).
Stage 1 ships the mechanism; SSE wiring is Stage 10.

Generic over the message type — Stage 1 uses :class:`CacheChange`, future
stages use ``shared.models.Event`` (e.g. for tailing ``events.jsonl``).
Type-parametric so we don't have to coerce everything to one envelope.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from weakref import WeakSet


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
