"""Per-stage MCP server — the four tools the agent calls.

Per design doc § HIL bridge § MCP tool surface. Three tools are
non-blocking; ``open_ask`` long-polls until the human answers or the item
is cancelled. All four are exposed over stdio via FastMCP and bound to a
fixed (job_slug, stage_id, root) tuple at process startup.

Tools:

- ``open_task``: writes ``stages/<sid>/tasks/<task_id>/task.json`` with
  ``state=RUNNING`` and returns ``{task_id}``.
- ``update_task``: mutates an existing ``task.json`` to the requested
  status.  Accepts an optional ``result`` dict that is persisted as a
  sidecar ``task-result.json``.
- ``open_ask``: writes ``hil/<item_id>.json`` (status ``awaiting``); awaits
  a filesystem modification that flips status to ``answered`` or
  ``cancelled``; returns the ``HilAnswer`` (or raises on cancellation).
- ``append_stages``: appends ``StageDefinition`` objects to
  ``stage-list.yaml`` for expander stages.

The module is also runnable: ``python -m dashboard.mcp <job_slug>
<stage_id> [--root <path>]`` enters stdio mode and registers the four
tools against a FastMCP server named ``hammock-dashboard``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal


class MCPToolError(Exception):
    """Raised by tool implementations to surface a structured error to MCP.

    Per implementation.md § Stage 6 acceptance: tool errors must surface as
    MCP errors (caller sees a JSON-RPC error) rather than silent failures
    or untyped Python exceptions.
    """


async def open_task(
    *,
    job_slug: str,
    stage_id: str,
    task_spec: str,
    worktree_branch: str,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Create a ``RUNNING`` task record and return its ``task_id``."""
    raise NotImplementedError


async def update_task(
    *,
    job_slug: str,
    stage_id: str,
    task_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, bool]:
    """Update an existing task record. ``status`` must be a ``TaskState``."""
    raise NotImplementedError


async def open_ask(
    *,
    job_slug: str,
    stage_id: str,
    kind: Literal["ask", "review", "manual-step"],
    task_id: str | None = None,
    root: Path | None = None,
    now: datetime | None = None,
    poll_interval: float = 0.1,
    timeout: float | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Long-poll: write a HIL item ``awaiting`` and block until answered.

    Returns the ``HilAnswer`` payload as a dict. Raises :class:`MCPToolError`
    if the item is cancelled before being answered (orphan-sweep scenario).
    """
    raise NotImplementedError


async def append_stages(
    *,
    job_slug: str,
    stages: list[dict[str, Any]],
    root: Path | None = None,
) -> dict[str, int | bool]:
    """Append ``StageDefinition`` objects to ``stage-list.yaml``.

    Returns ``{"ok": True, "count": N}``. The compiler validates the
    appended stages on its next pass; this tool only appends.
    """
    raise NotImplementedError


def build_server(
    *,
    job_slug: str,
    stage_id: str,
    root: Path | None = None,
) -> Any:  # FastMCP server instance
    """Construct a FastMCP server with the four tools bound to (job, stage)."""
    raise NotImplementedError


async def run_stdio(
    *,
    job_slug: str,
    stage_id: str,
    root: Path | None = None,
) -> None:
    """Run the per-stage server over stdio. Blocks until the client disconnects."""
    raise NotImplementedError


def main(argv: list[str] | None = None) -> None:
    """Entry point for ``python -m dashboard.mcp``."""
    parser = argparse.ArgumentParser(prog="dashboard.mcp")
    parser.add_argument("job_slug")
    parser.add_argument("stage_id")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    asyncio.run(
        run_stdio(job_slug=args.job_slug, stage_id=args.stage_id, root=args.root)
    )


__all__ = [
    "MCPToolError",
    "append_stages",
    "build_server",
    "main",
    "open_ask",
    "open_task",
    "run_stdio",
    "update_task",
]
