"""MCP ``ask_human`` roundtrip — Stage 1 §1.6.

A focused test of ``dashboard/mcp/server.py`` in isolation: spawn the
MCP server as a subprocess, send an ``ask_human`` (or v0 ``open_ask`` —
will be renamed in Stage 4) tool call over stdio, assert the pending
marker appears on disk, simulate the dashboard POST having succeeded
(remove the marker + write the variable envelope), assert the MCP
server returns the answer over stdio.

Does NOT need the engine driver, real Claude, gh, or git.

Step 0 stub — test raises NotImplementedError until Step 1 + Step 2
implement the subprocess + stdio framing helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_ask_human_writes_pending_then_returns_answer(tmp_path: Path) -> None:
    """End-to-end roundtrip on the MCP server alone.

    1. Spawn dashboard.mcp.server with HAMMOCK_ROOT=tmp_path.
    2. Send an MCP initialize handshake + tool-list discovery.
    3. Send ask_human(question="Pick A or B?") — server should write
       the pending marker and BLOCK the response.
    4. From the test, remove the pending marker and write the variable
       envelope (simulating dashboard POST having succeeded).
    5. Assert the MCP server unblocks and returns the answer string.
    """
    raise NotImplementedError
