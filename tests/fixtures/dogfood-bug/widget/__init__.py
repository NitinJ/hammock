"""Minimal demo package with one intentional bug for the hammock dogfood run."""

from __future__ import annotations


def parse_range(s: str) -> list[int]:
    """Parse 'a-b' into the inclusive integer range [a, b]."""
    a, b = s.split("-")
    return list(range(int(a), int(b)))
