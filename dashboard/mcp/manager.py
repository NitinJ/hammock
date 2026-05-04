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

_SERVER_NAME = "hammock-dashboard"


@dataclass
class ServerHandle:
    """Per-stage MCP server descriptor returned by :meth:`MCPManager.spawn`."""

    job_slug: str
    stage_id: str
    root: Path
    channel: Channel
    mcp_config: dict[str, Any] = field(default_factory=dict)


class MCPManager:
    """Engine-side bookkeeping for per-stage MCP servers.

    Construction is parameterless; pass per-stage args to :meth:`spawn`.
    The default ``mcp_config`` template launches ``python -m dashboard.mcp``
    over stdio with the job/stage/root encoded as CLI args. Tests can pass
    a custom ``python_executable``.
    """

    def __init__(
        self,
        *,
        python_executable: str | None = None,
        module: str = "dashboard.mcp",
    ) -> None:
        self._python = python_executable or default_python_executable()
        self._module = module
        self._live: dict[tuple[str, str], ServerHandle] = {}

    def spawn(
        self,
        *,
        job_slug: str,
        stage_id: str,
        root: Path | None = None,
    ) -> ServerHandle:
        resolved_root = root if root is not None else _default_root()
        channel = Channel(job_slug=job_slug, stage_id=stage_id, root=resolved_root)
        cfg = self._build_mcp_config(job_slug, stage_id, resolved_root)
        handle = ServerHandle(
            job_slug=job_slug,
            stage_id=stage_id,
            root=resolved_root,
            channel=channel,
            mcp_config=cfg,
        )
        self._live[(job_slug, stage_id)] = handle
        return handle

    def dispose(self, handle: ServerHandle) -> None:
        self._live.pop((handle.job_slug, handle.stage_id), None)

    async def run(self, *, poll_interval: float = 60.0) -> None:
        """Long-running janitor loop.

        v0 ships a no-op (sleeps until cancelled) so the dashboard
        lifespan has a single shape for "background subsystem". A
        v1+ stage adds the real cleanup work — reaping per-stage MCP
        server descriptors whose enclosing job has reached a terminal
        state, plus orphan-detection for spawn calls that never
        called ``dispose``.

        Cancellation propagates via :class:`asyncio.CancelledError`.
        """
        import asyncio
        import logging

        log = logging.getLogger(__name__)
        log.info("mcp manager started — poll_interval=%.1fs (v0 no-op)", poll_interval)
        try:
            while True:
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            log.info("mcp manager cancelled")
            raise

    def _build_mcp_config(self, job_slug: str, stage_id: str, root: Path) -> dict[str, Any]:
        return {
            "mcpServers": {
                _SERVER_NAME: {
                    "command": self._python,
                    "args": [
                        "-m",
                        self._module,
                        job_slug,
                        stage_id,
                        "--root",
                        str(root),
                    ],
                }
            }
        }


def default_python_executable() -> str:
    """The interpreter path used by spawned MCP servers."""
    return sys.executable


def _default_root() -> Path:
    from shared.paths import HAMMOCK_ROOT

    return HAMMOCK_ROOT


__all__ = ["MCPManager", "ServerHandle", "default_python_executable"]
