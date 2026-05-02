"""Tests for ``shared.slug``."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from shared.slug import (
    MAX_SLUG_LENGTH,
    SLUG_PATTERN,
    SlugDerivationError,
    derive_slug,
    is_valid_slug,
    validate_slug,
)

# Worked examples from design doc -------------------------------------------


def test_derive_slug_design_doc_example() -> None:
    """Design doc § Project Registry: figur-Backend_v2 → figur-backend-v2."""
    assert derive_slug("/home/nitin/workspace/figur-Backend_v2") == "figur-backend-v2"


@pytest.mark.parametrize(
    "import_path,expected",
    [
        ("/path/to/MyRepo", "myrepo"),
        ("/a/b/c/Hello-World", "hello-world"),
        ("/a/b/Hello___World", "hello-world"),
        ("/a/b/--leading-hyphens--", "leading-hyphens"),
        ("/a/b/snake_case_thing", "snake-case-thing"),
        ("/a/b/UPPER", "upper"),
        ("/repos/x", "x"),
    ],
)
def test_derive_slug_table(import_path: str, expected: str) -> None:
    assert derive_slug(import_path) == expected


def test_derive_slug_truncates_to_max_length() -> None:
    long = "/a/b/" + "x" * (MAX_SLUG_LENGTH + 20)
    assert len(derive_slug(long)) <= MAX_SLUG_LENGTH


def test_derive_slug_empty_basename_rejected() -> None:
    with pytest.raises(SlugDerivationError):
        derive_slug("/tmp/____")


# Validation ----------------------------------------------------------------


def test_validate_slug_accepts_canonical() -> None:
    validate_slug("figur-backend-v2")
    validate_slug("a")
    validate_slug("a1")


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "Has-Caps",
        "trailing-",
        "-leading",
        "double--hyphen",
        "underscore_here",
        "x" * (MAX_SLUG_LENGTH + 1),
        "spaces here",
        "punct.",
    ],
)
def test_validate_slug_rejects_bad(bad: str) -> None:
    with pytest.raises(ValueError):
        validate_slug(bad)


def test_is_valid_slug() -> None:
    assert is_valid_slug("ok-slug")
    assert not is_valid_slug("Bad Slug")


# Property tests ------------------------------------------------------------


@given(
    name=st.text(
        alphabet=st.characters(blacklist_characters="/\x00", blacklist_categories=("Cc",)),
        min_size=1,
        max_size=120,
    )
)
def test_derive_slug_either_succeeds_or_raises(name: str) -> None:
    """Property: derivation either produces a valid slug or raises SlugDerivationError."""
    try:
        slug = derive_slug(f"/tmp/{name}")
    except SlugDerivationError:
        return
    # If it returned, it must be valid.
    assert SLUG_PATTERN.match(slug)
    assert 1 <= len(slug) <= MAX_SLUG_LENGTH
    assert not slug.startswith("-")
    assert not slug.endswith("-")
    assert "--" not in slug


@given(slug=st.text(min_size=1, max_size=40))
def test_validate_round_trip(slug: str) -> None:
    """Property: is_valid_slug(s) ⇔ validate_slug(s) does not raise."""
    valid = is_valid_slug(slug)
    if valid:
        validate_slug(slug)  # must not raise
    else:
        with pytest.raises(ValueError):
            validate_slug(slug)
