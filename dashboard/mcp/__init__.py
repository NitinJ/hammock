"""Dashboard MCP server — v1 slim surface.

Per design-patch §9.6: one tool (``ask_human``); one MCP server per
job; agents inherit ``HAMMOCK_NODE_ID`` (and optionally
``HAMMOCK_NODE_ITER``) so the server scopes tool calls correctly.

The v0 four-tool surface (``open_task``, ``update_task``, ``open_ask``,
``append_stages``) is gone; the dashboard's stream pane surfaces
sub-task progress from agent stdout directly.
"""

from __future__ import annotations

from dashboard.mcp.manager import MCPManager, ServerHandle
from dashboard.mcp.server import ask_human, build_server, run_stdio

__all__ = [
    "MCPManager",
    "ServerHandle",
    "ask_human",
    "build_server",
    "run_stdio",
]
