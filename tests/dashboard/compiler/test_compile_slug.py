"""Tests for the v1 job-slug derivation in ``dashboard.compiler.compile``.

The slug shape is ``<YYYY-MM-DD>-<job_type>-<title-slug>-<suffix>``. The
suffix is a random hex tag added so two jobs submitted with the same
title on the same day don't clash on the filesystem path
(``~/.hammock/jobs/<slug>/``). Without it, the user hit "job dir
already exists" or — worse — silently picked up the wrong dir.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from dashboard.compiler.compile import _derive_slug


def test_derive_slug_includes_random_suffix() -> None:
    """Two slugs with identical inputs must differ in their suffix."""
    s1 = _derive_slug("fix-bug", "black color missing")
    s2 = _derive_slug("fix-bug", "black color missing")
    assert s1 != s2, f"identical inputs produced colliding slugs: {s1}"


def test_derive_slug_shape_is_predictable_with_explicit_suffix() -> None:
    """When the caller pins ``suffix`` (tests, replay), the slug is
    fully deterministic."""
    now = datetime(2026, 5, 8, tzinfo=UTC)
    slug = _derive_slug("fix-bug", "Black color is missing", now=now, suffix="abc123")
    assert slug == "2026-05-08-fix-bug-black-color-is-missing-abc123"


def test_derive_slug_random_suffix_matches_expected_alphabet() -> None:
    """Default suffix is 6 hex chars (3 bytes from secrets.token_hex)."""
    slug = _derive_slug("fix-bug", "x")
    # shape: 2026-05-08-fix-bug-x-<6 hex>
    suffix = slug.rsplit("-", 1)[-1]
    assert re.fullmatch(r"[0-9a-f]{6}", suffix), (
        f"unexpected suffix shape {suffix!r} on slug {slug!r}"
    )


def test_derive_slug_empty_title_falls_back_to_untitled() -> None:
    """Title containing only punctuation slugifies to empty → 'untitled'."""
    now = datetime(2026, 5, 8, tzinfo=UTC)
    slug = _derive_slug("fix-bug", "!!!", now=now, suffix="aaaa11")
    assert slug == "2026-05-08-fix-bug-untitled-aaaa11"
