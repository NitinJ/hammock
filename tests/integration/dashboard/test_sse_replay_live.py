"""SSE replay + live phase tests — Stage 1 §1.6.

Verifies:

- Replay returns events at or before the time of subscription.
- Reconnect with ``Last-Event-ID: <seq>`` returns no duplicates and no
  gaps after that seq.
- Live phase delivers new events scripted via ``FakeEngine``.
- Scope filters: a job-scope subscriber does not receive global-scope
  events; a stage-scope subscriber receives only its stage's events.

Step 0 stubs — tests raise NotImplementedError until Step 1 fills them.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


@pytest.mark.asyncio
async def test_replay_returns_pre_subscribe_events(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    raise NotImplementedError


@pytest.mark.asyncio
async def test_replay_then_live_seq_continuous(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    raise NotImplementedError


@pytest.mark.asyncio
async def test_reconnect_with_last_event_id_no_duplicates(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    raise NotImplementedError


@pytest.mark.asyncio
async def test_job_scope_does_not_receive_global_events(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    raise NotImplementedError


@pytest.mark.asyncio
async def test_live_event_appears_for_subscriber(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    raise NotImplementedError
