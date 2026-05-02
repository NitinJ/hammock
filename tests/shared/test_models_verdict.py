"""Tests for ``shared.models.verdict``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.models import ReviewConcern, ReviewVerdict
from tests.shared.factories import make_review_verdict


def test_factory_roundtrip() -> None:
    v = make_review_verdict()
    assert ReviewVerdict.model_validate_json(v.model_dump_json()) == v


def test_three_verdict_values_locked() -> None:
    """Design doc canonicalises exactly three verdict values."""
    for ok in ("approved", "needs-revision", "rejected"):
        ReviewVerdict(verdict=ok, summary="x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ReviewVerdict.model_validate({"verdict": "blessed", "summary": "x"})


def test_severity_values() -> None:
    for sev in ("blocker", "major", "minor"):
        ReviewConcern(severity=sev, concern="x", location="y")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ReviewConcern.model_validate({"severity": "critical", "concern": "x", "location": "y"})


def test_empty_summary_rejected() -> None:
    with pytest.raises(ValidationError):
        ReviewVerdict(verdict="approved", summary="")
