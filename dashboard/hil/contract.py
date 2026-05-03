"""HIL contract — get_open_items and submit_answer.

Per design doc § HIL bridge and § Presentation plane:

    get_open_items(filter) -> list[HilItem]
    submit_answer(item_id, answer) -> HilItem

``submit_answer`` is idempotent: re-submitting the identical answer is a
no-op; re-submitting a *different* answer to an already-answered item raises
:class:`ConflictError`.

The contract is a thin layer over the in-memory cache (reads) and
``shared.atomic`` disk writes (mutations). Disk writes trigger the filesystem
watcher, which keeps the cache eventually consistent. ``submit_answer`` also
calls ``cache.apply_change`` immediately for same-request consistency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dashboard.hil.state_machine import transition
from dashboard.state.cache import Cache, ChangeKind
from shared.atomic import atomic_write_json
from shared.models.hil import HilAnswer, HilItem
from shared.paths import hil_item_path


class NotFoundError(Exception):
    """Raised when an item_id is not found in the cache."""


class ConflictError(Exception):
    """Raised when submit_answer is called with a different answer on an already-answered item."""


@dataclass
class HilFilter:
    """Optional filter for :meth:`HilContract.get_open_items`."""

    status: Literal["awaiting", "answered", "cancelled"] | None = field(default="awaiting")
    kind: Literal["ask", "review", "manual-step"] | None = field(default=None)
    job_slug: str | None = field(default=None)
    stage_id: str | None = field(default=None)


class HilContract:
    """Domain contract for the HIL plane.

    Parameters
    ----------
    cache:
        Live in-memory cache (read source and post-write invalidation target).
    root:
        Hammock root directory. Defaults to the cache's root if not given.
    """

    def __init__(self, *, cache: Cache, root: Path | None = None) -> None:
        self._cache = cache
        self._root = root if root is not None else cache.root

    def get_open_items(self, filter: HilFilter | None = None) -> list[HilItem]:
        """Return HIL items matching *filter* (defaults to all ``awaiting``)."""
        f = filter if filter is not None else HilFilter()
        items = self._cache.list_hil(job_slug=f.job_slug, status=f.status)
        if f.kind is not None:
            items = [i for i in items if i.kind == f.kind]
        if f.stage_id is not None:
            items = [i for i in items if i.stage_id == f.stage_id]
        return items

    def submit_answer(self, item_id: str, answer: HilAnswer) -> HilItem:
        """Transition ``awaiting → answered``, persist answer, return updated item.

        Idempotent for identical answers. Raises :class:`ConflictError` if the
        item is already answered with a different answer. Raises
        :class:`NotFoundError` if *item_id* is unknown.
        """
        item = self._cache.get_hil(item_id)
        if item is None:
            raise NotFoundError(f"HIL item {item_id!r} not found")

        if item.status == "answered":
            if item.answer == answer:
                return item
            raise ConflictError(f"HIL item {item_id!r} already answered with a different answer")

        # Raises InvalidTransitionError if item.status is "cancelled"
        updated = transition(item, "answered")
        updated = updated.model_copy(update={"answer": answer, "answered_at": datetime.now(UTC)})

        job_slug = self._cache.hil_job_slug(item_id)
        if job_slug is None:
            raise NotFoundError(f"cannot determine job for HIL item {item_id!r}")

        path = hil_item_path(job_slug, item_id, root=self._root)
        atomic_write_json(path, updated)
        self._cache.apply_change(path, ChangeKind.MODIFIED)
        return updated
