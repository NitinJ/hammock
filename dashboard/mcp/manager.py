"""MCP server lifecycle manager — v1 per-job spawn.

Per design-patch §9.6 / impl-patch §Stage 4:

- One MCP server process per job (was per stage in v0).
- Spawned at job submit / driver bootstrap; torn down on job terminal
  state.
- Each spawned agent subprocess inherits the MCP socket env var **plus**
  ``HAMMOCK_NODE_ID`` and (when inside a loop body) ``HAMMOCK_NODE_ITER``
  so the server can scope tool calls to the calling node.

This module owns the engine-side bookkeeping (``mcp_config`` dict for
agent session settings, plus the ``ServerHandle`` registry). The actual
server subprocess is launched by the agent (Claude Code) from the
``mcp_config``.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SERVER_NAME = "hammock-dashboard"


@dataclass
class ServerHandle:
    """Per-job MCP server descriptor returned by :meth:`MCPManager.spawn`."""

    job_slug: str
    root: Path
    mcp_config: dict[str, Any] = field(default_factory=dict)


class MCPManager:
    """Engine-side bookkeeping for per-job MCP servers."""

    def __init__(
        self,
        *,
        python_executable: str | None = None,
        module: str = "dashboard.mcp",
    ) -> None:
        self._python = python_executable or default_python_executable()
        self._module = module
        self._live: dict[str, ServerHandle] = {}

    def spawn(
        self,
        *,
        job_slug: str,
        root: Path | None = None,
    ) -> ServerHandle:
        """Register a per-job MCP server descriptor. Idempotent: a second
        spawn for the same job_slug returns the existing handle."""
        existing = self._live.get(job_slug)
        if existing is not None:
            return existing
        resolved_root = root if root is not None else _default_root()
        cfg = self._build_mcp_config(job_slug, resolved_root)
        handle = ServerHandle(job_slug=job_slug, root=resolved_root, mcp_config=cfg)
        self._live[job_slug] = handle
        return handle

    def dispose(self, handle: ServerHandle) -> None:
        self._live.pop(handle.job_slug, None)

    def get(self, job_slug: str) -> ServerHandle | None:
        return self._live.get(job_slug)

    def live_count(self) -> int:
        return len(self._live)

    async def run(self, *, poll_interval: float = 60.0) -> None:
        """Long-running janitor. v1 ships a no-op (sleeps until cancelled);
        a future stage adds reaping for terminal-state jobs."""
        import asyncio
        import logging

        log = logging.getLogger(__name__)
        log.info("mcp manager started — poll_interval=%.1fs (v1 no-op)", poll_interval)
        try:
            while True:
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            log.info("mcp manager cancelled")
            raise

    def _build_mcp_config(self, job_slug: str, root: Path) -> dict[str, Any]:
        return {
            "mcpServers": {
                _SERVER_NAME: {
                    "command": self._python,
                    "args": [
                        "-m",
                        self._module,
                        job_slug,
                        "--root",
                        str(root),
                    ],
                }
            }
        }


def default_python_executable() -> str:
    return sys.executable


def _default_root() -> Path:
    from shared.paths import HAMMOCK_ROOT

    return HAMMOCK_ROOT


__all__ = ["MCPManager", "ServerHandle", "default_python_executable"]
