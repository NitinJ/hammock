"""Tests for ``dashboard.state.pubsub``."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from dashboard.state.pubsub import InProcessPubSub


@dataclass(frozen=True)
class _Msg:
    kind: str
    payload: int


@pytest.mark.asyncio
async def test_subscribe_then_publish_delivers() -> None:
    bus: InProcessPubSub[_Msg] = InProcessPubSub()
    sub = bus.subscribe("global")
    bus.publish("global", _Msg("a", 1))
    msg = await asyncio.wait_for(anext(sub), timeout=1.0)
    assert msg == _Msg("a", 1)


@pytest.mark.asyncio
async def test_publish_to_no_subscriber_is_noop() -> None:
    bus: InProcessPubSub[_Msg] = InProcessPubSub()
    bus.publish("nobody-listening", _Msg("a", 1))  # must not raise
    assert bus.subscriber_count("nobody-listening") == 0


@pytest.mark.asyncio
async def test_scope_isolation() -> None:
    bus: InProcessPubSub[_Msg] = InProcessPubSub()
    sub_a = bus.subscribe("scope-a")
    sub_b = bus.subscribe("scope-b")

    bus.publish("scope-a", _Msg("a", 1))
    bus.publish("scope-b", _Msg("b", 2))

    msg_a = await asyncio.wait_for(anext(sub_a), timeout=1.0)
    msg_b = await asyncio.wait_for(anext(sub_b), timeout=1.0)
    assert msg_a == _Msg("a", 1)
    assert msg_b == _Msg("b", 2)


@pytest.mark.asyncio
async def test_late_subscriber_does_not_get_history() -> None:
    bus: InProcessPubSub[_Msg] = InProcessPubSub()
    bus.publish("scope", _Msg("a", 1))  # nobody hears it
    sub = bus.subscribe("scope")
    bus.publish("scope", _Msg("b", 2))
    msg = await asyncio.wait_for(anext(sub), timeout=1.0)
    assert msg == _Msg("b", 2)


@pytest.mark.asyncio
async def test_multiple_subscribers_each_receive() -> None:
    bus: InProcessPubSub[_Msg] = InProcessPubSub()
    s1 = bus.subscribe("scope")
    s2 = bus.subscribe("scope")
    bus.publish("scope", _Msg("x", 1))
    m1 = await asyncio.wait_for(anext(s1), timeout=1.0)
    m2 = await asyncio.wait_for(anext(s2), timeout=1.0)
    assert m1 == m2 == _Msg("x", 1)
    assert bus.subscriber_count("scope") == 2


@pytest.mark.asyncio
async def test_aclose_unregisters() -> None:
    bus: InProcessPubSub[_Msg] = InProcessPubSub()
    sub = bus.subscribe("scope")
    assert bus.subscriber_count("scope") == 1
    await sub.aclose()
    assert bus.subscriber_count("scope") == 0


@pytest.mark.asyncio
async def test_slow_subscriber_does_not_block_fast() -> None:
    """A bounded slow-subscriber queue dropping doesn't hold up the fast one."""
    bus: InProcessPubSub[_Msg] = InProcessPubSub()
    fast = bus.subscribe("scope")
    slow = bus.subscribe("scope", maxsize=1)

    # Fill slow's queue, then publish more — slow drops but fast keeps up.
    bus.publish("scope", _Msg("x", 1))
    bus.publish("scope", _Msg("x", 2))  # slow's queue full → dropped
    bus.publish("scope", _Msg("x", 3))  # ditto

    fast_msgs = [await asyncio.wait_for(anext(fast), timeout=1.0) for _ in range(3)]
    assert [m.payload for m in fast_msgs] == [1, 2, 3]
    # slow received exactly the first
    slow_msg = await asyncio.wait_for(anext(slow), timeout=1.0)
    assert slow_msg.payload == 1


@pytest.mark.asyncio
async def test_publish_order_preserved_per_subscriber() -> None:
    bus: InProcessPubSub[_Msg] = InProcessPubSub()
    sub = bus.subscribe("scope")
    for i in range(50):
        bus.publish("scope", _Msg("x", i))
    seen = [(await asyncio.wait_for(anext(sub), timeout=1.0)).payload for _ in range(50)]
    assert seen == list(range(50))
