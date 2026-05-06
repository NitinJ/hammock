"""Disk-contract tests — Stage 1 §1.6.

Verifies the dashboard watcher correctly classifies every path the v1
disk layout exposes. Step 0 stub: tests raise NotImplementedError so the
suite fails loudly until Step 1 fills in the test bodies.

When this file is fully written (Step 1), each test must:

1. Use ``fake_engine`` to write the path under test.
2. Wait for the watcher to pick it up (poll the cache or pubsub).
3. Assert the path was classified into the expected ClassifiedPath kind
   and that the cache invalidation matches the v1 surface.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


@pytest.mark.asyncio
async def test_job_json_classified(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_node_state_classified(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_loop_indexed_variable_envelope_classified(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_top_level_variable_envelope_classified(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_events_jsonl_append_classified(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_pending_marker_classified(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_pending_marker_removal_classified(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_node_run_attempt_artefact_classified(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
