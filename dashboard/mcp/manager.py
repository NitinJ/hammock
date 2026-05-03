"""MCP server lifecycle manager.

Per implementation.md § Stage 6 task DAG: ``MCPManager.spawn(stage_id)``
returns a per-stage handle; ``MCPManager.dispose(handle)`` tears it down.

The handle bundles two things the stage runner needs:

- the :class:`~dashboard.mcp.channel.Channel` for engine-side nudge writes;
- the ``mcp_config`` dict to embed in the agent's session settings so
  Claude Code launches ``python -m dashboard.mcp <job> <stage>`` over stdio
  on demand.

The actual server subprocess is launched by Claude Code from the
``mcp_config`` (one stdio process per stage, per spec). ``MCPManager`` is
therefore mostly stateful bookkeeping for the engine side.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dashboard.mcp.channel import Channel


@dataclass
class ServerHandle:
    """Per-stage MCP server descriptor returned by :meth:`MCPManager.spawn`."""

    job_slug: str
    stage_id: str
    root: Path
    channel: Channel
    mcp_config: dict[str, Any] = field(default_factory=dict)


class MCPManager:
    """Owns the engine-side bookkeeping for per-stage MCP servers.

    Construction is parameterless; pass per-stage args to :meth:`spawn`.
    The default ``mcp_config`` template launches ``python -m dashboard.mcp``
    over stdio with the job/stage/root encoded as CLI args; tests can pass
    a custom ``python_executable`` (e.g., a recorded fixture launcher).
    """

    def __init__(
        self,
        *,
        python_executable: str | None = None,
        module: str = "dashboard.mcp",
    ) -> None:
        raise NotImplementedError

    def spawn(
        self,
        *,
        job_slug: str,
        stage_id: str,
        root: Path | None = None,
    ) -> ServerHandle:
        """Build the per-stage server descriptor."""
        raise NotImplementedError

    def dispose(self, handle: ServerHandle) -> None:
        """Release resources owned by *handle*. Idempotent."""
        raise NotImplementedError


def default_python_executable() -> str:
    """The interpreter path used by spawned MCP servers."""
    return sys.executable


__all__ = ["MCPManager", "ServerHandle", "default_python_executable"]
