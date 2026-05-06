"""HIL Path B — implicit (agent-initiated) HIL, dashboard-side — Stage 1 §1.6.

Path B in production is: agent calls MCP ``ask_human`` → MCP server
writes a pending marker → dashboard sees it → human submits → engine's
``submit_hil_answer`` removes the marker → MCP server reads the answer
back to the calling agent.

Stage 1 covers everything from "pending marker exists on disk" through
"submission removes the marker." The MCP server's own role is in
``tests/integration/mcp/test_ask_human_roundtrip.py``.

From the dashboard's perspective Path B is identical to Path A — same
``submit_hil_answer`` code path. These tests exist to lock the contract
in case the marker shape ever diverges.

Step 0 stubs — tests raise NotImplementedError until Step 1 fills them.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


@pytest.mark.asyncio
async def test_implicit_hil_marker_visible_in_api(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")


@pytest.mark.asyncio
async def test_implicit_hil_answer_removes_marker(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 3 — disk-first dashboard fills this in")
