"""Channel push — engine nudges and free-form chat into a running stage.

Per design doc § HIL bridge § The blocking model — *``--channels`` is
reserved for traffic that does not correspond to a structured ask.* The
dashboard MCP server is the writer; on-disk storage is
``stages/<sid>/nudges.jsonl``. Stage 5's ``RealStageRunner`` picks up
nudges between turns (the consumer-side mechanism is owned by the agent
runner; ``Channel`` only owns the write).

The ``notify`` callable on a :class:`Channel` is the optional in-process
bridge for tests and future live-injection mechanisms — ``Channel.push``
appends to ``nudges.jsonl`` first, then awaits ``notify(message)`` if set.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.atomic import atomic_append_jsonl
from shared.paths import stage_nudges_jsonl

NudgeKind = Literal["nudge", "chat"]
NudgeSource = Literal["dashboard", "engine", "human"]

NotifyCallback = Callable[["NudgeMessage"], Awaitable[None] | None]


class NudgeMessage(BaseModel):
    """A single nudge entry persisted to ``nudges.jsonl``."""

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0)
    timestamp: datetime
    stage_id: str = Field(min_length=1)
    kind: NudgeKind
    source: NudgeSource
    text: str


class Channel:
    """Per-stage channel writer.

    ``push`` is concurrency-safe (an asyncio lock serialises writes) and
    sequence numbers are stable across process restarts because the next
    ``seq`` is seeded from the on-disk tail at construction time.
    """

    def __init__(
        self,
        *,
        job_slug: str,
        stage_id: str,
        root: Path | None = None,
        notify: NotifyCallback | None = None,
    ) -> None:
        self._job_slug = job_slug
        self._stage_id = stage_id
        self._path = stage_nudges_jsonl(job_slug, stage_id, root=root)
        self._notify = notify
        self._lock = asyncio.Lock()
        self._next_seq = _last_seq(self._path) + 1

    @property
    def path(self) -> Path:
        return self._path

    async def push(
        self,
        *,
        kind: NudgeKind,
        text: str,
        source: NudgeSource = "dashboard",
        timestamp: datetime | None = None,
    ) -> NudgeMessage:
        async with self._lock:
            seq = self._next_seq
            self._next_seq += 1
            msg = NudgeMessage(
                seq=seq,
                timestamp=timestamp if timestamp is not None else datetime.now(tz=UTC),
                stage_id=self._stage_id,
                kind=kind,
                source=source,
                text=text,
            )
            atomic_append_jsonl(self._path, msg)

        if self._notify is not None:
            result = self._notify(msg)
            if inspect.isawaitable(result):
                await result
        return msg


def _last_seq(path: Path) -> int:
    """Return the highest ``seq`` already written to *path* (-1 if empty)."""
    if not path.exists():
        return -1
    last = -1
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            seq = payload.get("seq")
            if isinstance(seq, int) and seq > last:
                last = seq
    except OSError:
        return -1
    return last


__all__ = ["Channel", "NudgeKind", "NudgeMessage", "NudgeSource"]
