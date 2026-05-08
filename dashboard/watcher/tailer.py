"""Filesystem tailer — watches the hammock root, fires pub/sub.

Per impl-patch §Stage 3: the watcher no longer updates an in-memory
cache. It classifies each path against the v1 layout
(``dashboard.state.classify``) and publishes a ``PathChange`` to the
relevant pub/sub scopes; subscribers (mainly the SSE handler) read disk
on demand to materialize responses.

Scopes (str keys):

- ``"global"`` — every change publishes here (cross-job views).
- ``"project:<slug>"``       — project state files.
- ``"job:<slug>"``            — job-level changes (state, vars, hil, events).
- ``"node:<job>/<node_id>"`` — node-scoped drilldown.

``events.jsonl`` appends are tailed and emitted as typed ``Event``
records via ``events_pubsub`` (separate channel).

``chat.jsonl`` (per-attempt agent transcript) appends are coalesced
to at most one event per (job_slug, node_id, iter_token, attempt)
within a ``CHAT_COALESCE_WINDOW_S`` window — the SSE consumer
refetches the whole chat on poke, so over-emitting just wastes work.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from watchfiles import Change, awatch

from dashboard.state.classify import (
    ChangeKind,
    ClassifiedPath,
    classify_path,
    scopes_for,
)
from shared.v1 import paths as v1_paths

CHAT_COALESCE_WINDOW_S: float = 0.5
"""chat_appended events collapse to one per (key, 500ms window).

Under steady-state agent streaming we'd otherwise emit one SSE message
per turn line. Refetch-on-poke means the consumer only needs to know
'something changed recently' — fine-grained pacing buys nothing."""

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class PathChange:
    """Pub/sub message published when a state file changes.

    Renamed from v0 ``CacheChange`` since there is no cache anymore;
    a backwards-compat alias is kept below for callers still using
    the old name.
    """

    path: Path
    kind: ChangeKind
    classified: ClassifiedPath


# Backwards-compat alias — referenced by older sse.py / tests until
# they migrate to PathChange.
CacheChange = PathChange


_WATCHFILES_TO_KIND: dict[Change, ChangeKind] = {
    Change.added: ChangeKind.ADDED,
    Change.modified: ChangeKind.MODIFIED,
    Change.deleted: ChangeKind.DELETED,
}


def to_change_kind(c: Change) -> ChangeKind:
    return _WATCHFILES_TO_KIND[c]


async def run(
    root: Path,
    pubsub: object,
    events_pubsub: object | None = None,
    *,
    stop_event: asyncio.Event | None = None,
    debounce_ms: int = 100,
    step_ms: int = 50,
    _watcher: AsyncIterator[set[tuple[Change, str]]] | None = None,
) -> None:
    """Watch *root* and publish changes until *stop_event* is set."""
    if _watcher is None:
        _watcher = awatch(
            root,
            stop_event=stop_event,
            debounce=debounce_ms,
            step=step_ms,
        )

    file_offsets: dict[Path, int] = {}
    # Prime offsets from existing events.jsonl files so the first
    # MODIFIED notification only delivers genuinely new appends.
    for existing in root.glob("jobs/*/events.jsonl"):
        with contextlib.suppress(OSError):
            file_offsets[existing] = existing.stat().st_size

    # Last emit time per (job_slug, node_id, iter_token, attempt) tuple
    # for chat_appended coalescing. Survives across batches.
    chat_last_emit: dict[tuple[str, str, str, int], float] = {}

    async for batch in _watcher:
        _process_batch(root, pubsub, events_pubsub, file_offsets, chat_last_emit, batch)


def _process_batch(
    root: Path,
    pubsub: object,
    events_pubsub: object | None,
    file_offsets: dict[Path, int],
    chat_last_emit: dict[tuple[str, str, str, int], float],
    batch: Iterable[tuple[Change, str]],
) -> None:
    publish = getattr(pubsub, "publish", None)
    if publish is None:
        raise TypeError("pubsub must implement .publish(scope, message)")
    events_publish: Any = getattr(events_pubsub, "publish", None)

    for change, raw_path in batch:
        path = Path(raw_path)
        try:
            kind = to_change_kind(change)
        except KeyError:
            LOG.debug("watcher: ignoring unknown change kind %s for %s", change, path)
            continue

        classified = classify_path(path, root)
        if classified.kind == "unknown":
            continue

        if classified.kind == "events_jsonl":
            if events_publish is not None and kind is not ChangeKind.DELETED:
                _tail_and_publish_events(path, classified, file_offsets, events_publish)
            continue

        # Coalesce chat.jsonl appends to one event per (key, CHAT_COALESCE_WINDOW_S).
        if classified.kind == "chat_jsonl" and not _should_emit_chat(
            classified, kind, chat_last_emit
        ):
            continue

        msg = PathChange(path=path, kind=kind, classified=classified)
        for scope in scopes_for(classified):
            publish(scope, msg)


def _should_emit_chat(
    classified: ClassifiedPath,
    kind: ChangeKind,
    chat_last_emit: dict[tuple[str, str, str, int], float],
) -> bool:
    """Return True iff this chat.jsonl change should publish.

    DELETED always publishes (cleanup signal). MODIFIED / ADDED collapse
    to one event per (job, node, iter_token, attempt) per
    ``CHAT_COALESCE_WINDOW_S`` window. Missing key fields shouldn't
    happen for a chat_jsonl classification, but we drop on incomplete
    metadata rather than emitting incoherent events.
    """
    if kind is ChangeKind.DELETED:
        return True
    if (
        classified.job_slug is None
        or classified.node_id is None
        or classified.iter_path is None
        or classified.attempt is None
    ):
        return False
    key = (
        classified.job_slug,
        classified.node_id,
        v1_paths.iter_token(classified.iter_path),
        classified.attempt,
    )
    now = time.monotonic()
    last = chat_last_emit.get(key)
    if last is not None and (now - last) < CHAT_COALESCE_WINDOW_S:
        return False
    chat_last_emit[key] = now
    return True


def _tail_and_publish_events(
    path: Path,
    classified: ClassifiedPath,
    file_offsets: dict[Path, int],
    events_publish: Any,
) -> None:
    """Read new bytes from *path* since last seen offset, parse and publish Events."""
    from shared.models import Event as _Event

    offset = file_offsets.get(path, 0)
    try:
        with path.open("rb") as f:
            f.seek(offset)
            new_bytes = f.read()
    except OSError as e:
        LOG.warning("watcher: cannot tail %s: %s", path, e)
        return

    last_newline = new_bytes.rfind(b"\n")
    if last_newline == -1:
        return  # No complete lines yet; hold the offset.
    complete_bytes = new_bytes[: last_newline + 1]
    file_offsets[path] = offset + last_newline + 1

    if classified.kind == "events_jsonl" and classified.job_slug:
        scopes = ["global", f"job:{classified.job_slug}"]
    else:
        return

    for raw_line in complete_bytes.decode("utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            event = _Event.model_validate(data)
        except Exception:
            continue
        for scope in scopes:
            events_publish(scope, event)
