"""Tiny demo program — sums an arbitrary number of integers.

Seed for the real-claude e2e test repo. Kept intentionally small so a
single agent stage can grok it; credible enough that fix-bug stages
have something real to land on.

Intentional bug: returns ``None`` when called with no arguments instead
of ``0``. The bundled ``test_no_args_returns_zero`` test fails as a
result. The e2e fix-bug request points the agent here.
"""

from __future__ import annotations


def add_integers(*nums: int) -> int | None:
    """Return the sum of the given integers."""
    if not nums:  # BUG: should return 0 for the empty case
        return None
    return sum(nums)
