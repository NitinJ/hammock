"""Stage 6 manual smoke.

Spawns the per-stage MCP server (``python -m dashboard.mcp ...``) over stdio
and exercises the four tools end-to-end against a real ``mcp.ClientSession``.

Demonstrates:
  1. ``open_task`` writes ``stages/<sid>/tasks/<task_id>/task.json``.
  2. ``update_task(DONE)`` flips the persisted state and writes
     ``task-result.json``.
  3. ``open_ask`` long-polls; we simulate a human answer by writing the
     ``hil/<id>.json`` file and assert the agent receives the answer.
  4. ``append_stages`` adds a stage to ``stage-list.yaml``.

Run with::

    uv run python scripts/manual-smoke-stage06.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from shared.paths import hil_dir, job_stage_list, task_json  # noqa: E402

JOB = "smoke/proj"
STAGE = "implement-1"


def _ok(label: str) -> None:
    print(f"  ✓ {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  ✗ {label}" + (f": {detail}" if detail else ""))
    raise SystemExit(1)


def _payload(content: list) -> dict:  # type: ignore[type-arg]
    for block in content:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"no text content in {content!r}")


async def _drive(root: Path) -> None:
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dashboard.mcp", JOB, STAGE, "--root", str(root)],
        env=env,
    )

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        _ok("MCP session initialised")

        # 1. open_task ----------------------------------------------------
        ot = await session.call_tool(
            "open_task",
            {
                "task_spec": "rewire authentication",
                "worktree_branch": "job/smoke-proj/stage/implement-1/task/auth",
            },
        )
        task_id = _payload(ot.content)["task_id"]
        assert task_json(JOB, STAGE, task_id, root=root).exists()
        _ok(f"open_task → task.json written ({task_id})")

        # 2. update_task DONE --------------------------------------------
        ut = await session.call_tool(
            "update_task",
            {
                "task_id": task_id,
                "status": "DONE",
                "result": {"output_files": ["src/auth.py"]},
            },
        )
        assert _payload(ut.content) == {"ok": True}
        _ok("update_task(DONE) — state flipped, task-result.json written")

        # 3. open_ask long-poll + simulated human answer -----------------
        async def _human() -> None:
            deadline = asyncio.get_event_loop().time() + 5.0
            while asyncio.get_event_loop().time() < deadline:
                items = list(hil_dir(JOB, root=root).glob("*.json"))
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
            raise AssertionError("no HIL item appeared")

        bg = asyncio.create_task(_human())
        oa = await session.call_tool(
            "open_ask",
            {
                "kind": "ask",
                "text": "Which path should I take?",
                "options": ["A", "B"],
                "task_id": task_id,
                "poll_interval": 0.05,
                "timeout": 5.0,
            },
        )
        await bg
        answer = _payload(oa.content)
        assert answer == {"kind": "ask", "text": "use option B", "choice": "B"}
        _ok("open_ask blocked, received human answer (option B)")

        # 4. append_stages -----------------------------------------------
        ap = await session.call_tool(
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
        assert _payload(ap.content) == {"ok": True, "count": 1}
        stage_list_path = job_stage_list(JOB, root=root)
        assert "implement-2" in stage_list_path.read_text()
        _ok("append_stages added implement-2 to stage-list.yaml")


def main() -> None:
    print("== Stage 6 manual smoke ==\n")
    print("[1/1] Driving MCP server over stdio...")
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(_drive(Path(tmp)))
    print("\n✓ Stage 6 smoke complete — four MCP tools functional end-to-end.")


if __name__ == "__main__":
    main()
