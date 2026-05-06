"""runs_if-skipped node tests — Stage 1 §1.6.

A node whose runs_if predicate evaluates false renders SKIPPED with the
recorded reason; downstream nodes that depend on it do not block the
job.

Step 0 stubs — tests raise NotImplementedError until Step 1 fills them.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


@pytest.mark.asyncio
async def test_skipped_node_renders_with_reason(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_skipped_node_does_not_block_downstream(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
