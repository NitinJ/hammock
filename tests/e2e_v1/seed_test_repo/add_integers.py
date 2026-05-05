"""Tiny demo module with a known bug used by later e2e_v1 stages
(T3+ once code-kind nodes land). T1 does not touch this file."""

from __future__ import annotations


def add_integers(*nums: int) -> int | None:
    """Return the sum of the given integers."""
    if not nums:  # BUG: should return 0 for the empty case
        return None
    return sum(nums)
