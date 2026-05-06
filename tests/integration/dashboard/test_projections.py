"""Projection tests — Stage 1 §1.6.

Given a scripted disk sequence via FakeEngine, the dashboard's HTTP
endpoints (``GET /api/jobs/:slug``, ``GET /api/jobs/:slug/nodes/:id``,
etc.) return the expected JSON shape, including loop iteration unrolling,
SKIPPED nodes, and resolved variable envelopes.

Step 0 stubs — tests raise NotImplementedError until Step 1 fills them.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


@pytest.mark.asyncio
async def test_job_overview_after_start(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")


@pytest.mark.asyncio
async def test_node_detail_running(dashboard: DashboardHandle, fake_engine: FakeEngine) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")


@pytest.mark.asyncio
async def test_node_detail_succeeded_with_envelope(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")


@pytest.mark.asyncio
async def test_node_detail_skipped_carries_reason(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")


@pytest.mark.asyncio
async def test_loop_iterations_unroll_in_overview(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")


@pytest.mark.asyncio
async def test_nested_loop_iterations_unroll(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
