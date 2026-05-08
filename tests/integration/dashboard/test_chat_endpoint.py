"""Tests for ``GET /api/jobs/{slug}/nodes/{node_id}/iter/{iter_token}/chat``.

Endpoint surfaces the per-attempt ``chat.jsonl`` (claude stream-json
output) so the dashboard can render the agent's turn-by-turn transcript
in the right pane.

Contract:
- Returns ``{turns: [...], attempt: <n>, has_chat: bool}``.
- Missing file (not-yet-run nodes) → ``has_chat: false``,
  ``turns: []``.
- Malformed JSONL line → skipped; valid lines still returned.
- ``?attempt=<n>`` selects an attempt; default 1.
- ``iter_token`` axis selects the iteration (``top`` for top-level
  executions, ``i<...>`` for loop-body executions). Bad token → 400.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.v1 import paths
from tests.integration.conftest import DashboardHandle


def _write_chat_jsonl(
    *,
    root: Path,
    job_slug: str,
    node_id: str,
    attempt: int,
    iter_path: tuple[int, ...] = (),
    lines: list[dict | str],
) -> Path:
    """Seed a chat.jsonl at the v1 attempt-dir path. ``lines`` may mix
    dicts (serialised to JSON) and raw strings (used for malformed-line
    tests).
    """
    attempt_dir = paths.node_attempt_dir(job_slug, node_id, attempt, iter_path, root=root)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    chat_path = attempt_dir / "chat.jsonl"
    out: list[str] = []
    for line in lines:
        if isinstance(line, str):
            out.append(line)
        else:
            out.append(json.dumps(line))
    chat_path.write_text("\n".join(out) + "\n")
    return chat_path


@pytest.mark.asyncio
async def test_chat_endpoint_returns_parsed_turns(dashboard: DashboardHandle) -> None:
    job_slug = "j1"
    node_id = "write-bug-report"
    paths.ensure_job_layout(job_slug, root=dashboard.root)
    _write_chat_jsonl(
        root=dashboard.root,
        job_slug=job_slug,
        node_id=node_id,
        attempt=1,
        lines=[
            {"type": "system", "subtype": "init", "session_id": "abc", "cwd": "/tmp"},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hello"}],
                },
            },
            {
                "type": "result",
                "is_error": False,
                "num_turns": 2,
                "total_cost_usd": 0.012,
            },
        ],
    )

    resp = await dashboard.client.get(f"/api/jobs/{job_slug}/nodes/{node_id}/iter/top/chat")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_chat"] is True
    assert body["attempt"] == 1
    turns = body["turns"]
    assert len(turns) == 3
    assert turns[0]["type"] == "system"
    assert turns[1]["type"] == "assistant"
    assert turns[1]["message"]["content"][0]["text"] == "Hello"
    assert turns[2]["type"] == "result"


@pytest.mark.asyncio
async def test_chat_endpoint_missing_file_has_chat_false(
    dashboard: DashboardHandle,
) -> None:
    """Not-yet-run nodes have no chat.jsonl on disk. The endpoint
    returns 200 with has_chat=False, not 404 — the frontend treats
    this as 'no transcript yet'."""
    paths.ensure_job_layout("j2", root=dashboard.root)
    resp = await dashboard.client.get("/api/jobs/j2/nodes/some-node/iter/top/chat")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_chat"] is False
    assert body["turns"] == []
    assert body["attempt"] == 1


@pytest.mark.asyncio
async def test_chat_endpoint_skips_malformed_lines(
    dashboard: DashboardHandle,
) -> None:
    """Claude can be killed mid-turn and write a partial JSON line. The
    endpoint must skip it rather than 500."""
    job_slug = "j3"
    node_id = "n"
    paths.ensure_job_layout(job_slug, root=dashboard.root)
    _write_chat_jsonl(
        root=dashboard.root,
        job_slug=job_slug,
        node_id=node_id,
        attempt=1,
        lines=[
            {"type": "system", "subtype": "init"},
            "{ not valid json",  # truncated mid-write
            {"type": "result", "is_error": False},
        ],
    )

    resp = await dashboard.client.get(f"/api/jobs/{job_slug}/nodes/{node_id}/iter/top/chat")
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_chat"] is True
    # Two valid turns; malformed line dropped.
    types = [t["type"] for t in body["turns"]]
    assert types == ["system", "result"]


@pytest.mark.asyncio
async def test_chat_endpoint_respects_attempt_query(dashboard: DashboardHandle) -> None:
    job_slug = "j4"
    node_id = "n"
    paths.ensure_job_layout(job_slug, root=dashboard.root)
    _write_chat_jsonl(
        root=dashboard.root,
        job_slug=job_slug,
        node_id=node_id,
        attempt=1,
        lines=[{"type": "system", "first_attempt": True}],
    )
    _write_chat_jsonl(
        root=dashboard.root,
        job_slug=job_slug,
        node_id=node_id,
        attempt=2,
        lines=[{"type": "system", "second_attempt": True}],
    )

    resp1 = await dashboard.client.get(
        f"/api/jobs/{job_slug}/nodes/{node_id}/iter/top/chat?attempt=1"
    )
    assert resp1.json()["turns"][0].get("first_attempt") is True
    resp2 = await dashboard.client.get(
        f"/api/jobs/{job_slug}/nodes/{node_id}/iter/top/chat?attempt=2"
    )
    assert resp2.json()["turns"][0].get("second_attempt") is True
    assert resp2.json()["attempt"] == 2


@pytest.mark.asyncio
async def test_chat_endpoint_iter_token_route(dashboard: DashboardHandle) -> None:
    """The iter-keyed route reads chat.jsonl from the matching iter
    directory. Top-level executions use the literal token 'top'."""
    job_slug = "j-iter"
    node_id = "leaf"
    paths.ensure_job_layout(job_slug, root=dashboard.root)

    # Top-level run.
    _write_chat_jsonl(
        root=dashboard.root,
        job_slug=job_slug,
        node_id=node_id,
        attempt=1,
        iter_path=(),
        lines=[{"type": "system", "scope": "top"}],
    )
    # Loop-body run at iter_path=(0,1).
    _write_chat_jsonl(
        root=dashboard.root,
        job_slug=job_slug,
        node_id=node_id,
        attempt=1,
        iter_path=(0, 1),
        lines=[{"type": "system", "scope": "i0_1"}],
    )

    resp_top = await dashboard.client.get(f"/api/jobs/{job_slug}/nodes/{node_id}/iter/top/chat")
    assert resp_top.status_code == 200
    assert resp_top.json()["turns"][0]["scope"] == "top"

    resp_loop = await dashboard.client.get(f"/api/jobs/{job_slug}/nodes/{node_id}/iter/i0_1/chat")
    assert resp_loop.status_code == 200
    assert resp_loop.json()["turns"][0]["scope"] == "i0_1"


@pytest.mark.asyncio
async def test_chat_endpoint_iter_token_bad_returns_400(dashboard: DashboardHandle) -> None:
    """Malformed iter_token (no leading 'i', non-digit chunks) -> 400."""
    paths.ensure_job_layout("j-bad", root=dashboard.root)
    resp = await dashboard.client.get("/api/jobs/j-bad/nodes/n/iter/garbage/chat")
    assert resp.status_code == 400
