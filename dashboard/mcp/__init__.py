"""Dashboard MCP server: four tools the agent calls during a stage.

Per design doc § HIL bridge § MCP tool surface and implementation.md § Stage 6.
The dashboard exposes ``open_task``, ``update_task``, ``open_ask``, and
``append_stages`` over stdio. One MCP server instance per active stage; the
agent's session connects via the per-session settings produced by the
:class:`~dashboard.mcp.manager.MCPManager`.
"""

from __future__ import annotations

from dashboard.mcp.channel import Channel, NudgeMessage
from dashboard.mcp.manager import MCPManager, ServerHandle
from dashboard.mcp.server import (
    append_stages,
    build_server,
    open_ask,
    open_task,
    run_stdio,
    update_task,
)

__all__ = [
    "Channel",
    "MCPManager",
    "NudgeMessage",
    "ServerHandle",
    "append_stages",
    "build_server",
    "open_ask",
    "open_task",
    "run_stdio",
    "update_task",
]
