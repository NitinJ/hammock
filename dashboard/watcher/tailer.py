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
import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path

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
    """
    if _watcher is None:
        _watcher = awatch(
            cache.root,
            stop_event=stop_event,
            debounce=debounce_ms,
            step=step_ms,
        )

    async for batch in _watcher:
        _process_batch(cache, pubsub, batch)


def _process_batch(
    cache: Cache,
    pubsub: object,
    batch: Iterable[tuple[Change, str]],
) -> None:
    publish = getattr(pubsub, "publish", None)
    if publish is None:
        raise TypeError("pubsub must implement .publish(scope, message)")

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

        msg = CacheChange(path=path, kind=kind, classified=classified)
        for scope in scopes_for(classified):
            publish(scope, msg)
