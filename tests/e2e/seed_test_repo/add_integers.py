"""Tiny demo program — sums an arbitrary number of integers.

Seed for the real-claude e2e test repo. Kept intentionally small so a
single agent stage can grok it; credible enough that fix-bug /
build-feature stages have something real to land on.
"""

from __future__ import annotations


def add_integers(*nums: int) -> int:
    """Return the sum of the given integers."""
    return sum(nums)
