"""Channel push — engine nudges and free-form chat into a running stage.

Per design doc § HIL bridge § The blocking model — *``--channels`` is
reserved for traffic that does not correspond to a structured ask.* The
dashboard MCP server is the writer; on-disk storage is
``stages/<sid>/nudges.jsonl``. Stage 5's ``RealStageRunner`` picks up nudges
between turns (the consumer-side mechanism is owned by the agent runner;
``Channel`` only owns the write).

The ``notify`` callable on a :class:`Channel` is the optional in-process
bridge for tests and future live-injection mechanisms — ``Channel.push``
appends to ``nudges.jsonl`` first, then awaits ``notify(message)`` if set.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.atomic import atomic_append_jsonl
from shared.paths import stage_nudges_jsonl

NudgeKind = Literal["nudge", "chat"]
NudgeSource = Literal["dashboard", "engine", "human"]


class NudgeMessage(BaseModel):
    """A single nudge entry persisted to ``nudges.jsonl``.

    ``seq`` is monotonic per stage; the channel maintains the counter in
    memory and seeds it from the last line on disk at construction.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0)
    timestamp: datetime
    stage_id: str = Field(min_length=1)
    kind: NudgeKind
    source: NudgeSource
    text: str


class Channel:
    """Per-stage channel writer.

    Construct one per active stage. ``push`` is concurrency-safe (an
    asyncio lock serialises writes) and sequence numbers are stable across
    process restarts because the next ``seq`` is seeded from the on-disk
    tail at construction time.
    """

    def __init__(
        self,
        *,
        job_slug: str,
        stage_id: str,
        root: Path | None = None,
        notify: Callable[[NudgeMessage], Awaitable[None]] | None = None,
    ) -> None:
        raise NotImplementedError

    @property
    def path(self) -> Path:
        """Resolved ``nudges.jsonl`` path for this stage."""
        raise NotImplementedError

    async def push(
        self,
        *,
        kind: NudgeKind,
        text: str,
        source: NudgeSource = "dashboard",
        timestamp: datetime | None = None,
    ) -> NudgeMessage:
        """Append a nudge to ``nudges.jsonl`` and fan out to ``notify``."""
        raise NotImplementedError


def _last_seq(path: Path) -> int:
    """Return the highest ``seq`` already written to *path* (-1 if empty)."""
    raise NotImplementedError


__all__ = ["Channel", "NudgeKind", "NudgeMessage", "NudgeSource", "stage_nudges_jsonl"]
