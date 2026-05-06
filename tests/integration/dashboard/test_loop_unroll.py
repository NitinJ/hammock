"""Loop iteration unrolling — Stage 1 §1.6.

Drive a loop with multiple iterations (and nested loops); assert the
projection unrolls iterations correctly and SSE scoping respects
iteration coordinates.

Step 0 stubs — tests raise NotImplementedError until Step 1 fills them.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


@pytest.mark.asyncio
async def test_outer_loop_three_iterations_unroll(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """An outer loop with body nodes at iter (0,), (1,), (2,) renders
    three indented sections in the API response."""
    pytest.skip("Stage 3 — disk-first dashboard fills this in")


@pytest.mark.asyncio
async def test_nested_loop_two_levels_unroll(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Outer loop iter 0 + inner loop iter 0,1 ; outer iter 1 + inner
    iter 0 — projection groups under the right outer iteration."""
    pytest.skip("Stage 3 — disk-first dashboard fills this in")


@pytest.mark.asyncio
async def test_loop_indexed_envelopes_resolve_per_iteration(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Variable envelopes at variables/loop_<id>_<var>_<i>.json are
    resolved into the per-iteration node detail response."""
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
