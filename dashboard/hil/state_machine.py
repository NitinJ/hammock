"""HIL state machine — pure transition logic.

Per design doc § HIL bridge § HIL lifecycle. Three states:

    awaiting → answered   (human submits answer)
    awaiting → cancelled  (orphan sweep on stage restart)

``answered`` and ``cancelled`` are terminal. Any other transition raises
:class:`InvalidTransitionError`.
"""

from __future__ import annotations

from typing import Literal

from shared.models.hil import HilItem

HilStatus = Literal["awaiting", "answered", "cancelled"]

_ALLOWED: dict[str, set[str]] = {
    "awaiting": {"answered", "cancelled"},
    "answered": set(),
    "cancelled": set(),
}


class InvalidTransitionError(Exception):
    """Raised when a requested state transition is not permitted."""


def transition(item: HilItem, new_status: HilStatus) -> HilItem:
    """Return a copy of *item* with ``status`` set to *new_status*.

    Raises :class:`InvalidTransitionError` if the transition is not allowed.
    """
    raise NotImplementedError
