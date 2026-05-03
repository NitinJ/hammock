"""Filesystem tailer — watches the hammock root, updates the cache, fires pub/sub.

Architecture (per design doc § Process structure):

  watchfiles.awatch(root)
       │
       ▼
  for change_kind, path in batch:
       cache.apply_change(path, change_kind)
       pubsub.publish(scope_for(classified), CacheChange(...))

Scopes (str keys):

- ``"global"`` — every state-file change is also published here so home / HIL
  queue / project-list views can update without subscribing per-job.
- ``"project:<slug>"``  — project state files.
- ``"job:<slug>"``      — job-level state and HIL items inside a job.
- ``"stage:<job>:<sid>"`` — stage state changes.

The watcher emits :class:`CacheChange` notifications, not full ``Event``
envelopes. Forwarding ``events.jsonl`` lines as typed
``shared.models.Event``s is Stage 10's SSE layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from watchfiles import Change, awatch

from dashboard.state.cache import Cache, ChangeKind, ClassifiedPath

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Notification envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheChange:
    """Pub/sub message published when a state file changes.

    Stage 1 uses this directly; Stage 10's SSE handlers translate it into
    wire-format events for browser clients.
    """

    path: Path
    kind: ChangeKind
    classified: ClassifiedPath


def scopes_for(classified: ClassifiedPath) -> list[str]:
    """Return every pub/sub scope that should be notified of this change.

    ``"global"`` is always included. Specific scopes are appended based on
    the change's identifying ids.
    """
    scopes = ["global"]
    if classified.kind == "project" and classified.project_slug is not None:
        scopes.append(f"project:{classified.project_slug}")
    elif classified.kind == "job" and classified.job_slug is not None:
        scopes.append(f"job:{classified.job_slug}")
    elif classified.kind == "stage":
        if classified.job_slug is not None:
            scopes.append(f"job:{classified.job_slug}")
        if classified.job_slug is not None and classified.stage_id is not None:
            scopes.append(f"stage:{classified.job_slug}:{classified.stage_id}")
    elif classified.kind == "hil" and classified.job_slug is not None:
        scopes.append(f"job:{classified.job_slug}")
    return scopes


# ---------------------------------------------------------------------------
# Change-kind translation (watchfiles → ours)
# ---------------------------------------------------------------------------


_WATCHFILES_TO_KIND: dict[Change, ChangeKind] = {
    Change.added: ChangeKind.ADDED,
    Change.modified: ChangeKind.MODIFIED,
    Change.deleted: ChangeKind.DELETED,
}


def to_change_kind(c: Change) -> ChangeKind:
    return _WATCHFILES_TO_KIND[c]


# ---------------------------------------------------------------------------
# The watcher loop
# ---------------------------------------------------------------------------


async def run(
    cache: Cache,
    pubsub: object,  # InProcessPubSub[CacheChange] — typed via Protocol below
    events_pubsub: object | None = None,  # InProcessPubSub[Event] for events.jsonl tail
    *,
    stop_event: asyncio.Event | None = None,
    debounce_ms: int = 100,
    step_ms: int = 50,
    _watcher: AsyncIterator[set[tuple[Change, str]]] | None = None,
) -> None:
    """Run the watcher loop until *stop_event* is set or the iterator ends.

    *debounce_ms* and *step_ms* are forwarded to ``watchfiles.awatch``;
    defaults are tuned for low-latency dashboard updates (the design's 100ms
    target). Tests override these via *_watcher*.

    Stage 12.5 (A5): *events_pubsub* receives typed ``Event`` records tailed
    from ``events.jsonl`` files.  Each file's byte offset is tracked so only
    new appends are published on each MODIFIED event (no duplicates on restart).
    """
    if _watcher is None:
        _watcher = awatch(
            cache.root,
            stop_event=stop_event,
            debounce=debounce_ms,
            step=step_ms,
        )

    file_offsets: dict[Path, int] = {}

    async for batch in _watcher:
        _process_batch(cache, pubsub, events_pubsub, file_offsets, batch)


def _process_batch(
    cache: Cache,
    pubsub: object,
    events_pubsub: object | None,
    file_offsets: dict[Path, int],
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
        try:
            classified = cache.apply_change(path, kind)
        except Exception:
            LOG.exception("watcher: cache.apply_change failed for %s", path)
            continue

        if classified.kind == "unknown":
            continue

        if classified.kind in ("events_jsonl", "events_jsonl_stage"):
            # Stage 12.5 (A5): tail new appends and publish typed Event records.
            if events_publish is not None and kind is not ChangeKind.DELETED:
                _tail_and_publish_events(path, classified, file_offsets, events_publish)
            continue

        msg = CacheChange(path=path, kind=kind, classified=classified)
        for scope in scopes_for(classified):
            publish(scope, msg)


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
            new_offset = offset + len(new_bytes)
    except OSError as e:
        LOG.warning("watcher: cannot tail %s: %s", path, e)
        return

    file_offsets[path] = new_offset

    # Determine scope set once for all events in this tail
    if classified.kind == "events_jsonl" and classified.job_slug:
        scopes = ["global", f"job:{classified.job_slug}"]
    elif (
        classified.kind == "events_jsonl_stage"
        and classified.job_slug
        and classified.stage_id
    ):
        scopes = [
            "global",
            f"job:{classified.job_slug}",
            f"stage:{classified.job_slug}:{classified.stage_id}",
        ]
    else:
        return

    for raw_line in new_bytes.decode("utf-8", errors="replace").splitlines():
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
