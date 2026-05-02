"""Slug derivation and validation.

Per design doc § Project Registry: identity is a path-derived immutable slug,
distinct from the mutable display name.

Rules (from design doc):
- Pattern: ``[a-z0-9-]+`` (kebab-case)
- Max length: 32 chars
- Derivation: lowercase basename → replace non-``[a-z0-9]`` runs with ``-``
  → collapse repeated ``-`` → strip leading/trailing ``-`` → truncate to 32
- Empty result (basename was all non-alphanumeric) → reject; caller prompts
  for an explicit slug
- Collision → caller prompts for an alternate

Worked example from design doc:
    /home/nitin/workspace/figur-Backend_v2  →  figur-backend-v2
"""

from __future__ import annotations

import re
from pathlib import Path

SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")
MAX_SLUG_LENGTH = 32

_NON_ALPHANUM = re.compile(r"[^a-z0-9]+")
_REPEATED_HYPHENS = re.compile(r"-+")


class SlugDerivationError(ValueError):
    """Raised when a slug cannot be derived from the input (empty result)."""


def derive_slug(import_path: str | Path) -> str:
    """Derive a slug from a filesystem import path.

    Raises :class:`SlugDerivationError` if the basename contains no
    alphanumeric characters (the result would be empty).
    """
    basename = Path(str(import_path)).name
    lowered = basename.lower()
    sanitized = _NON_ALPHANUM.sub("-", lowered)
    collapsed = _REPEATED_HYPHENS.sub("-", sanitized)
    stripped = collapsed.strip("-")
    truncated = stripped[:MAX_SLUG_LENGTH].rstrip("-")
    if not truncated:
        raise SlugDerivationError(
            f"cannot derive slug from {import_path!r}: basename has no alphanumerics"
        )
    return truncated


def validate_slug(slug: str) -> None:
    """Validate a slug against the canonical pattern and length.

    Raises :class:`ValueError` with a human-readable message when invalid.
    Returns ``None`` when valid.
    """
    if not slug:
        raise ValueError("slug must be non-empty")
    if len(slug) > MAX_SLUG_LENGTH:
        raise ValueError(f"slug exceeds {MAX_SLUG_LENGTH} chars: {slug!r}")
    if not SLUG_PATTERN.match(slug):
        raise ValueError(
            f"slug must match {SLUG_PATTERN.pattern!r} (kebab-case, lowercase): {slug!r}"
        )
    if slug.startswith("-") or slug.endswith("-"):
        raise ValueError(f"slug must not start or end with '-': {slug!r}")
    if "--" in slug:
        raise ValueError(f"slug must not contain consecutive hyphens: {slug!r}")


def is_valid_slug(slug: str) -> bool:
    try:
        validate_slug(slug)
    except ValueError:
        return False
    return True
