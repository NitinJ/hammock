"""Channel tests — engine nudge writes to ``nudges.jsonl``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dashboard.mcp.channel import Channel, NudgeMessage
from shared.paths import stage_nudges_jsonl

# Helpers --------------------------------------------------------------------


def _read_lines(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# Tests ----------------------------------------------------------------------


async def test_push_writes_nudges_jsonl(hammock_root: Path) -> None:
    ch = Channel(job_slug="proj/feat", stage_id="implement-1", root=hammock_root)

    msg = await ch.push(kind="nudge", text="please use --strict")

    assert isinstance(msg, NudgeMessage)
    assert msg.text == "please use --strict"
    assert msg.kind == "nudge"
    assert msg.source == "dashboard"
    assert msg.stage_id == "implement-1"
    assert msg.seq == 0

    nudges = stage_nudges_jsonl("proj/feat", "implement-1", root=hammock_root)
    rows = _read_lines(nudges)
    assert len(rows) == 1
    assert rows[0]["text"] == "please use --strict"
    assert rows[0]["seq"] == 0


async def test_push_seq_monotonic(hammock_root: Path) -> None:
    ch = Channel(job_slug="proj/feat", stage_id="implement-1", root=hammock_root)

    a = await ch.push(kind="nudge", text="one")
    b = await ch.push(kind="chat", text="two", source="human")
    c = await ch.push(kind="nudge", text="three", source="engine")

    assert (a.seq, b.seq, c.seq) == (0, 1, 2)
    rows = _read_lines(stage_nudges_jsonl("proj/feat", "implement-1", root=hammock_root))
    assert [r["seq"] for r in rows] == [0, 1, 2]
    assert rows[1]["source"] == "human"
    assert rows[2]["kind"] == "nudge"


async def test_push_resumes_seq_from_disk(hammock_root: Path) -> None:
    """A fresh Channel reads the on-disk tail to seed its next seq."""
    ch1 = Channel(job_slug="proj/feat", stage_id="implement-1", root=hammock_root)
    await ch1.push(kind="nudge", text="zero")
    await ch1.push(kind="nudge", text="one")

    ch2 = Channel(job_slug="proj/feat", stage_id="implement-1", root=hammock_root)
    msg = await ch2.push(kind="nudge", text="two")
    assert msg.seq == 2


async def test_push_concurrent_serialised(hammock_root: Path) -> None:
    """Concurrent pushes get distinct, ordered seqs and one line each."""
    import asyncio

    ch = Channel(job_slug="proj/feat", stage_id="implement-1", root=hammock_root)

    async def _push(i: int) -> NudgeMessage:
        return await ch.push(kind="nudge", text=f"msg-{i}")

    results = await asyncio.gather(*[_push(i) for i in range(20)])
    seqs = sorted(m.seq for m in results)
    assert seqs == list(range(20))

    rows = _read_lines(stage_nudges_jsonl("proj/feat", "implement-1", root=hammock_root))
    assert len(rows) == 20


async def test_push_invokes_notify(hammock_root: Path) -> None:
    seen: list[NudgeMessage] = []

    async def _notify(msg: NudgeMessage) -> None:
        seen.append(msg)

    ch = Channel(
        job_slug="proj/feat",
        stage_id="implement-1",
        root=hammock_root,
        notify=_notify,
    )
    msg = await ch.push(kind="nudge", text="hello")

    assert seen == [msg]


async def test_push_writes_before_notify(hammock_root: Path) -> None:
    """If ``notify`` raises, the on-disk write still committed."""
    ch = Channel(
        job_slug="proj/feat",
        stage_id="implement-1",
        root=hammock_root,
        notify=lambda _msg: _raise(),
    )

    with pytest.raises(RuntimeError, match="boom"):
        await ch.push(kind="nudge", text="written")

    rows = _read_lines(stage_nudges_jsonl("proj/feat", "implement-1", root=hammock_root))
    assert len(rows) == 1
    assert rows[0]["text"] == "written"


async def _raise() -> None:
    raise RuntimeError("boom")


def test_path_property(hammock_root: Path) -> None:
    ch = Channel(job_slug="proj/feat", stage_id="implement-1", root=hammock_root)
    assert ch.path == stage_nudges_jsonl("proj/feat", "implement-1", root=hammock_root)


async def test_timestamp_explicit_used(hammock_root: Path) -> None:
    ch = Channel(job_slug="proj/feat", stage_id="implement-1", root=hammock_root)
    fixed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    msg = await ch.push(kind="nudge", text="t", timestamp=fixed)
    assert msg.timestamp == fixed
