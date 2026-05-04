"""HilItem creation helpers shared between the MCP ``open_ask`` tool
and the JobDriver's ``_block_on_human`` stage-block path (P5 — real-claude
e2e precondition track).

Earlier the only writer was ``dashboard.mcp.server.open_ask`` (the
agent-initiated path); ``_block_on_human`` wrote ``stage.json`` +
``job.json`` only and the answer endpoint 404'd because no HilItem
existed. This module factors the persistence shape so future schema
changes update one place.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path

from shared import paths
from shared.atomic import atomic_write_json
from shared.models.hil import HilItem, ManualStepQuestion


def make_hil_id(kind: str, stamp: datetime) -> str:
    """Return a HilItem id of the form ``<kind>_<iso>_<token>``.

    ``kind`` should be the de-hyphenated kind name so the id is one
    word (e.g. ``manualstep``, ``ask``, ``review``). Mirrors the shape
    used by ``dashboard.mcp.server._make_hil_id`` so list views sort
    consistently across writers.
    """
    return f"{kind}_{stamp.strftime('%Y-%m-%dT%H:%M:%S')}_{secrets.token_hex(3)}"


def create_stage_block_hil_item(
    *,
    job_slug: str,
    stage_id: str,
    instructions: str,
    root: Path | None = None,
    now: datetime | None = None,
) -> HilItem:
    """Create + persist a manual-step HilItem for a stage transitioning
    to BLOCKED_ON_HUMAN, returning the persisted item.

    Caller is responsible for emitting any ``hil_item_opened`` event
    (the JobDriver does this; ``open_ask`` writes via a different path
    and v0 doesn't emit there). Atomic write; safe under crash.
    """
    stamp = now if now is not None else datetime.now(tz=UTC)
    item_id = make_hil_id("manualstep", stamp)
    item = HilItem(
        id=item_id,
        kind="manual-step",
        stage_id=stage_id,
        created_at=stamp,
        status="awaiting",
        question=ManualStepQuestion(
            kind="manual-step",
            instructions=instructions,
        ),
    )
    path = paths.hil_item_path(job_slug, item_id, root=root)
    atomic_write_json(path, item)
    return item
