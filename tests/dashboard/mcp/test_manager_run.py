"""Tests for MCPManager.run() — v0 minimal janitor loop.

Per `docs/v0-alignment-report.md` Plan #7: the dashboard lifespan
needs a long-running entry point on MCPManager. v0 ships a no-op
loop that sleeps until cancelled (placeholder for v1+ janitor work
like reaping orphaned per-stage MCP server descriptors). Cancellation
must propagate cleanly so lifespan shutdown is fast.
"""

from __future__ import annotations

import asyncio

from dashboard.mcp.manager import MCPManager


async def test_manager_run_cancels_cleanly() -> None:
    mgr = MCPManager()
    task = asyncio.create_task(mgr.run(poll_interval=0.05))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass
    assert task.done()
