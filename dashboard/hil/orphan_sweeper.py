"""Orphan sweeper — cancel awaiting HIL items on stage restart.

Per design doc § HIL bridge § Crash semantics:

    When a stage restarts, the runner sweeps all ``awaiting`` HIL items
    belonging to that stage to ``cancelled``.

The sweeper operates directly on the filesystem (reads glob, writes JSON)
rather than through the cache so it can run before the watcher has
re-synced. The watcher picks up the cancellations on its next scan.
"""

from __future__ import annotations

import json
from pathlib import Path

from shared.atomic import atomic_write_text
from shared.paths import hil_dir


class OrphanSweeper:
    """Cancels orphaned ``awaiting`` HIL items for a given stage.

    Parameters
    ----------
    root:
        Hammock root directory.
    """

    def __init__(self, *, root: Path | None = None) -> None:
        self._root = root

    def sweep(self, job_slug: str, stage_id: str) -> list[str]:
        """Cancel all ``awaiting`` HIL items for *stage_id* under *job_slug*.

        Returns the list of item IDs that were cancelled. Items already in a
        terminal state (``answered``, ``cancelled``) are left untouched.
        """
        directory = hil_dir(job_slug, root=self._root)
        if not directory.exists():
            return []

        cancelled: list[str] = []
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                continue

            if not isinstance(payload, dict):
                continue
            if payload.get("status") != "awaiting":
                continue
            if payload.get("stage_id") != stage_id:
                continue

            payload["status"] = "cancelled"
            atomic_write_text(path, json.dumps(payload, indent=2) + "\n")
            item_id = path.stem
            cancelled.append(item_id)

        return cancelled
