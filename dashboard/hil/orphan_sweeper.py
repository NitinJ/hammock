"""Orphan sweeper — cancel awaiting HIL items on stage restart.

Per design doc § HIL bridge § Crash semantics:

    When a stage restarts, the runner sweeps all ``awaiting`` HIL items
    belonging to that stage to ``cancelled``.

The sweeper operates directly on the filesystem (reads glob, writes JSON)
rather than through the cache so it can run before the watcher has
re-synced. The watcher picks up the cancellations on its next scan.
"""

from __future__ import annotations

from pathlib import Path


class OrphanSweeper:
    """Cancels orphaned ``awaiting`` HIL items for a given stage.

    Parameters
    ----------
    root:
        Hammock root directory.
    """

    def __init__(self, *, root: Path | None = None) -> None:
        raise NotImplementedError

    def sweep(self, job_slug: str, stage_id: str) -> list[str]:
        """Cancel all ``awaiting`` HIL items for *stage_id* under *job_slug*.

        Returns the list of item IDs that were cancelled. Items already in a
        terminal state (``answered``, ``cancelled``) are left untouched.
        """
        raise NotImplementedError
