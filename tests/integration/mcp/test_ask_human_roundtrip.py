"""MCP ``ask_human`` roundtrip — Stage 4 fills in Stage 1 §1.6 stub.

Stage 4 ships the v1 single-tool MCP surface. This test exercises the
``ask_human`` flow against the actual ``dashboard.mcp.server.ask_human``
function (without spawning the full FastMCP stdio server — that lives
behind ``run_stdio`` and would require an MCP client to drive end-to-end).

Coverage:

- Pending marker is written under ``<root>/jobs/<job>/asks/<call_id>.json``
  with question + node scope (``HAMMOCK_NODE_ID``, ``HAMMOCK_NODE_ITER``).
- ``ask_human`` blocks until the marker payload mutates to include
  ``answer``.
- The returned string matches what the submitter wrote.
- Marker is removed after the call returns.
- Missing ``HAMMOCK_NODE_ID`` raises ``MCPToolError``.
- Loop iter context is preserved on the marker.
- ``MCPManager.spawn`` is per-job and idempotent.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dashboard.mcp.server import MCPToolError, ask_human, asks_dir


async def _wait_for_marker(asks: Path, timeout: float = 5.0) -> Path:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.05)
        if asks.is_dir():
            files = list(asks.glob("*.json"))
            if files:
                return files[0]
    raise AssertionError("ask_human did not write a pending marker")


@pytest.mark.asyncio
async def test_ask_human_writes_pending_then_returns_answer(tmp_path: Path) -> None:
    job_slug = "j1"
    env = {"HAMMOCK_NODE_ID": "implement-loop"}

    task = asyncio.create_task(
        ask_human(
            job_slug=job_slug,
            question="Pick A or B?",
            root=tmp_path,
            env=env,
            poll_interval=0.05,
        )
    )

    marker = await _wait_for_marker(asks_dir(job_slug, root=tmp_path))
    payload = json.loads(marker.read_text())
    assert payload["question"] == "Pick A or B?"
    assert payload["node_id"] == "implement-loop"
    assert payload["iter"] is None

    payload["answer"] = "B"
    marker.write_text(json.dumps(payload))

    answer = await asyncio.wait_for(task, timeout=5.0)
    assert answer == "B"
    assert not marker.exists()


@pytest.mark.asyncio
async def test_ask_human_carries_loop_iter_in_env(tmp_path: Path) -> None:
    job_slug = "j-loop"
    env = {"HAMMOCK_NODE_ID": "implement", "HAMMOCK_NODE_ITER": "2,0"}

    task = asyncio.create_task(
        ask_human(
            job_slug=job_slug,
            question="confirm?",
            root=tmp_path,
            env=env,
            poll_interval=0.05,
        )
    )

    marker = await _wait_for_marker(asks_dir(job_slug, root=tmp_path))
    payload = json.loads(marker.read_text())
    assert payload["node_id"] == "implement"
    assert payload["iter"] == "2,0"

    payload["answer"] = "yes"
    marker.write_text(json.dumps(payload))
    answer = await asyncio.wait_for(task, timeout=5.0)
    assert answer == "yes"


@pytest.mark.asyncio
async def test_ask_human_missing_node_id_raises(tmp_path: Path) -> None:
    with pytest.raises(MCPToolError, match=r"HAMMOCK_NODE_ID"):
        await ask_human(
            job_slug="j",
            question="hi",
            root=tmp_path,
            env={},
            poll_interval=0.05,
        )


@pytest.mark.asyncio
async def test_ask_human_timeout_raises(tmp_path: Path) -> None:
    with pytest.raises(MCPToolError, match=r"timeout"):
        await ask_human(
            job_slug="j",
            question="hi",
            root=tmp_path,
            env={"HAMMOCK_NODE_ID": "n"},
            poll_interval=0.05,
            timeout=0.3,
        )


def test_manager_per_job_spawn_is_idempotent(tmp_path: Path) -> None:
    from dashboard.mcp.manager import MCPManager

    mgr = MCPManager()
    h1 = mgr.spawn(job_slug="alpha", root=tmp_path)
    h2 = mgr.spawn(job_slug="alpha", root=tmp_path)
    assert h1 is h2
    assert mgr.live_count() == 1

    h3 = mgr.spawn(job_slug="beta", root=tmp_path)
    assert h3 is not h1
    assert mgr.live_count() == 2

    mgr.dispose(h1)
    assert mgr.live_count() == 1
    assert mgr.get("alpha") is None


def test_manager_mcp_config_does_not_carry_stage_id(tmp_path: Path) -> None:
    """Stage 4: per-job spawn — mcp_config args drop the v0 stage_id."""
    from dashboard.mcp.manager import MCPManager

    mgr = MCPManager()
    h = mgr.spawn(job_slug="alpha", root=tmp_path)
    args = h.mcp_config["mcpServers"]["hammock-dashboard"]["args"]
    assert "alpha" in args
    assert "--root" in args
    # v0 had ``[..., "<job>", "<stage_id>", "--root", ...]``. v1 doesn't.
    job_idx = args.index("alpha")
    assert args[job_idx + 1] == "--root"
