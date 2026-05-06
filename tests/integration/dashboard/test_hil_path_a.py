"""HIL Path A — explicit human-actor node round-trip — Stage 1 §1.6.

The full disk-side flow:

1. ``FakeEngine.request_hil(node_id, type_name)`` writes a pending
   marker.
2. The dashboard exposes the gate via ``GET /api/hil/<job>``.
3. The human POSTs an answer via ``POST /api/hil/<job>/<id>/answer``.
4. ``engine.v1.hil.submit_hil_answer`` validates the typed payload,
   writes the variable envelope, and removes the pending marker
   atomically.
5. SSE subscribers see the lifecycle events.
6. ``FakeEngine.assert_hil_answered(node_id)`` confirms the disk state.

Step 0 stubs — tests raise NotImplementedError until Step 1 fills them.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


@pytest.mark.asyncio
async def test_review_verdict_round_trip(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Pending → GET (sees gate) → POST answer → pending gone, envelope on disk."""
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_post_answer_emits_sse_event(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_post_invalid_payload_is_rejected_pending_remains(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_get_hil_lists_pending_for_job(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
@pytest.mark.asyncio
async def test_loop_indexed_hil_round_trip(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """A HIL gate inside a loop body: pending marker references the
    iteration; submission writes a loop-indexed envelope."""
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
