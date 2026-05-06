"""Per-job MCP server — v1 slim surface (one tool: ``ask_human``).

Per design-patch §9.6 / impl-patch §Stage 4.

The dropped tools (``open_task``, ``update_task``, ``open_ask`` (renamed),
``append_stages``) are gone. v1's static DAG removes ``append_stages``;
the dashboard's stream pane surfaces sub-task progress from agent stdout
directly so ``open_task`` / ``update_task`` are unnecessary.

``ask_human`` flow:

1. Agent calls ``ask_human(question)``.
2. Server reads ``HAMMOCK_NODE_ID`` (and optional ``HAMMOCK_NODE_ITER``)
   from env to scope the call.
3. Server writes ``<root>/jobs/<job>/asks/<call_id>.json`` with the
   question + node scope.
4. Server polls the same path; when the file's content shape changes
   from ``{question, node_id, ...}`` to a payload containing ``answer``,
   the server reads it, removes the marker, returns the answer string.

Server is bound to a fixed ``(job_slug, root)`` at startup. Spawned via
``python -m dashboard.mcp <job_slug> [--root <path>]``.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.atomic import atomic_write_text
from shared.v1 import paths as v1_paths

_SERVER_NAME = "hammock-dashboard"
_NODE_ID_ENV = "HAMMOCK_NODE_ID"
_NODE_ITER_ENV = "HAMMOCK_NODE_ITER"


class MCPToolError(Exception):
    """Raised by tool implementations to surface a structured error to MCP."""


def asks_dir(job_slug: str, *, root: Path) -> Path:
    return v1_paths.job_dir(job_slug, root=root) / "asks"


def ask_marker_path(job_slug: str, call_id: str, *, root: Path) -> Path:
    return asks_dir(job_slug, root=root) / f"{call_id}.json"


def _make_call_id(now: datetime, node_id: str) -> str:
    """Stable id for one ask: timestamp + node + 6 hex chars of entropy.
    Distinguishes successive ask_human calls from the same node within
    a single iteration."""
    return f"ask_{now.strftime('%Y-%m-%dT%H:%M:%S')}_{node_id}_{secrets.token_hex(3)}"


def _scope_from_env(env: dict[str, str]) -> tuple[str, str | None]:
    node_id = env.get(_NODE_ID_ENV)
    if not node_id:
        raise MCPToolError(
            f"missing {_NODE_ID_ENV} env var — server must be spawned by an "
            "agent subprocess that sets the calling node's id"
        )
    iter_str = env.get(_NODE_ITER_ENV) or None
    return node_id, iter_str


async def ask_human(
    *,
    job_slug: str,
    question: str,
    root: Path,
    env: dict[str, str] | None = None,
    poll_interval: float = 0.1,
    timeout: float | None = None,
    now: datetime | None = None,
) -> str:
    """Block until the human submits an answer; return it as a string.

    Args:
        job_slug: the enclosing job. The MCP server is spawned per-job.
        question: prose for the human.
        root: hammock root.
        env: env-var dict carrying ``HAMMOCK_NODE_ID`` and optionally
             ``HAMMOCK_NODE_ITER``. Defaults to ``os.environ``.
        poll_interval: seconds between disk reads (test override).
        timeout: optional seconds before raising ``MCPToolError``.

    Pending marker shape (written by this fn):
        ``{"question": str, "node_id": str, "iter": str|None,
           "created_at": iso8601}``

    Answer shape (written externally by the dashboard / test):
        ``{"answer": str}``  — same path; payload swap signals completion.
    """
    if env is None:
        env = dict(os.environ)
    node_id, iter_str = _scope_from_env(env)

    stamp = now if now is not None else datetime.now(UTC)
    call_id = _make_call_id(stamp, node_id)

    asks_dir(job_slug, root=root).mkdir(parents=True, exist_ok=True)
    marker = ask_marker_path(job_slug, call_id, root=root)
    payload = {
        "question": question,
        "node_id": node_id,
        "iter": iter_str,
        "created_at": stamp.isoformat(),
    }
    atomic_write_text(marker, json.dumps(payload, indent=2))

    deadline = asyncio.get_event_loop().time() + timeout if timeout is not None else None
    while True:
        await asyncio.sleep(poll_interval)
        try:
            data = json.loads(marker.read_text())
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and "answer" in data:
            answer = data["answer"]
            if not isinstance(answer, str):
                raise MCPToolError(
                    f"answer for {call_id!r} has wrong type: {type(answer).__name__}"
                )
            # Best-effort cleanup; submitter may have already removed it.
            with contextlib.suppress(FileNotFoundError):
                marker.unlink()
            return answer
        if deadline is not None and asyncio.get_event_loop().time() >= deadline:
            raise MCPToolError(f"ask_human timeout waiting on {call_id!r}")


def build_server(*, job_slug: str, root: Path) -> Any:
    """Construct a FastMCP server with the v1 single-tool surface."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(_SERVER_NAME)

    async def _ask_human(
        question: str,
        poll_interval: float = 0.1,
        timeout: float | None = None,
    ) -> str:
        try:
            return await ask_human(
                job_slug=job_slug,
                question=question,
                root=root,
                poll_interval=poll_interval,
                timeout=timeout,
            )
        except MCPToolError as exc:
            raise ValueError(str(exc)) from exc

    server.add_tool(_ask_human, name="ask_human", description=ask_human.__doc__ or "")
    return server


async def run_stdio(*, job_slug: str, root: Path) -> None:
    """Run the per-job server over stdio."""
    server = build_server(job_slug=job_slug, root=root)
    await server.run_stdio_async()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dashboard.mcp")
    parser.add_argument("job_slug")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    root = args.root if args.root is not None else _default_root()
    asyncio.run(run_stdio(job_slug=args.job_slug, root=root))


def _default_root() -> Path:
    from shared.paths import HAMMOCK_ROOT

    return HAMMOCK_ROOT


__all__ = [
    "MCPToolError",
    "ask_human",
    "ask_marker_path",
    "asks_dir",
    "build_server",
    "main",
    "run_stdio",
]
