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
from pathlib import Path
from typing import Literal

from shared.models.hil import HilAnswer, HilItem


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
        Hammock root directory. Defaults to the cache's ``_root`` if not given.
    """

    def __init__(self, *, cache: object, root: Path | None = None) -> None:
        raise NotImplementedError

    def get_open_items(self, filter: HilFilter | None = None) -> list[HilItem]:
        """Return HIL items matching *filter* (defaults to all ``awaiting``)."""
        raise NotImplementedError

    def submit_answer(self, item_id: str, answer: HilAnswer) -> HilItem:
        """Transition ``awaiting → answered``, persist answer, return updated item.

        Idempotent for identical answers. Raises :class:`ConflictError` if the
        item is already answered with a different answer. Raises
        :class:`NotFoundError` if *item_id* is unknown.
        """
        raise NotImplementedError
