"""``python -m dashboard.mcp`` entry point.

Forwards to :func:`dashboard.mcp.server.main` so Claude Code can launch the
per-stage MCP server via the standard ``python -m`` invocation.
"""

from __future__ import annotations

from dashboard.mcp.server import main

if __name__ == "__main__":
    main()
