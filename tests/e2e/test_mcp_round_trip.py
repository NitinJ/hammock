"""E2E: spawn the per-stage MCP server and drive it over stdio.

Mirrors the integration test in implementation.md § Stage 6 T4: launch a
fake stage that calls ``open_task`` → ``update_task(DONE)`` → ``open_ask``,
simulate a human answer by writing the HilItem file, assert the agent
receives the answer.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from shared.paths import hil_dir

JOB = "proj-feat"
STAGE = "implement-1"


@asynccontextmanager
async def _client(root: Path) -> AsyncIterator[ClientSession]:
    """Spawn ``python -m dashboard.mcp ...`` and yield an initialised session."""
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dashboard.mcp", JOB, STAGE, "--root", str(root)],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _result_payload(content_blocks: list) -> dict:
    """Pull the JSON body out of the first text content block."""
    for block in content_blocks:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"no text content in {content_blocks!r}")


async def test_round_trip_open_update_ask(hammock_root: Path) -> None:
    async with _client(hammock_root) as session:
        # ----- open_task ------------------------------------------------
        ot = await session.call_tool(
            "open_task",
            {
                "task_spec": "implement the feature",
                "worktree_branch": "job/proj-feat/stage/implement-1/task/feat",
            },
        )
        ot_payload = _result_payload(ot.content)
        task_id = ot_payload["task_id"]
        assert task_id

        # ----- update_task DONE ----------------------------------------
        ut = await session.call_tool(
            "update_task",
            {"task_id": task_id, "status": "DONE", "result": {"ok": True}},
        )
        assert _result_payload(ut.content) == {"ok": True}

        # ----- open_ask (long-poll) — simulate human answer ------------
        async def _answer() -> None:
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                items = list(hil_dir(JOB, root=hammock_root).glob("*.json"))
                if items:
                    payload = json.loads(items[0].read_text())
                    payload["status"] = "answered"
                    payload["answer"] = {
                        "kind": "ask",
                        "text": "use option B",
                        "choice": "B",
                    }
                    payload["answered_at"] = datetime.now(tz=UTC).isoformat()
                    items[0].write_text(json.dumps(payload))
                    return
                await asyncio.sleep(0.05)

        bg = asyncio.create_task(_answer())
        oa = await session.call_tool(
            "open_ask",
            {
                "kind": "ask",
                "text": "Which option?",
                "task_id": task_id,
                "options": ["A", "B"],
                "poll_interval": 0.05,
                "timeout": 5.0,
            },
        )
        await bg

        ans = _result_payload(oa.content)
        assert ans["kind"] == "ask"
        assert ans["text"] == "use option B"
        assert ans["choice"] == "B"


async def test_round_trip_append_stages(hammock_root: Path) -> None:
    async with _client(hammock_root) as session:
        res = await session.call_tool(
            "append_stages",
            {
                "stages": [
                    {
                        "id": "implement-2",
                        "worker": "agent",
                        "inputs": {"required": [], "optional": None},
                        "outputs": {"required": []},
                    }
                ]
            },
        )
        assert _result_payload(res.content) == {"ok": True, "count": 1}
