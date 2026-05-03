"""Tests for ``shared.artifact_validators``."""

from __future__ import annotations

import json
from pathlib import Path

from shared.artifact_validators import REGISTRY, _non_empty, _review_verdict_schema


def test_registry_contains_expected_names() -> None:
    assert "non-empty" in REGISTRY
    assert "review-verdict-schema" in REGISTRY


# ---------------------------------------------------------------------------
# non-empty
# ---------------------------------------------------------------------------


def test_non_empty_passes_nonempty_text(tmp_path: Path) -> None:
    p = tmp_path / "out.txt"
    p.write_text("hello")
    assert _non_empty(p) is None


def test_non_empty_fails_empty_file(tmp_path: Path) -> None:
    p = tmp_path / "out.txt"
    p.write_bytes(b"")
    assert _non_empty(p) is not None


def test_non_empty_fails_whitespace_only(tmp_path: Path) -> None:
    p = tmp_path / "out.txt"
    p.write_text("   \n  ")
    assert _non_empty(p) is not None


def test_non_empty_fails_trivially_empty_json_object(tmp_path: Path) -> None:
    p = tmp_path / "out.json"
    p.write_text("{}")
    assert _non_empty(p) is not None


def test_non_empty_fails_trivially_empty_json_list(tmp_path: Path) -> None:
    p = tmp_path / "out.json"
    p.write_text("[]")
    assert _non_empty(p) is not None


def test_non_empty_fails_json_null(tmp_path: Path) -> None:
    p = tmp_path / "out.json"
    p.write_text("null")
    assert _non_empty(p) is not None


def test_non_empty_fails_json_empty_string(tmp_path: Path) -> None:
    p = tmp_path / "out.json"
    p.write_text('""')
    assert _non_empty(p) is not None


def test_non_empty_passes_nonempty_json(tmp_path: Path) -> None:
    p = tmp_path / "out.json"
    p.write_text('{"result": "done"}')
    assert _non_empty(p) is None


def test_non_empty_returns_error_on_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.txt"
    assert _non_empty(p) is not None


# ---------------------------------------------------------------------------
# review-verdict-schema
# ---------------------------------------------------------------------------

_VALID_VERDICT = {
    "verdict": "approved",
    "summary": "Looks good.",
    "unresolved_concerns": [],
    "addressed_in_this_iteration": [],
}


def test_review_verdict_schema_passes_valid(tmp_path: Path) -> None:
    p = tmp_path / "review.json"
    p.write_text(json.dumps(_VALID_VERDICT))
    assert _review_verdict_schema(p) is None


def test_review_verdict_schema_passes_needs_revision(tmp_path: Path) -> None:
    p = tmp_path / "review.json"
    p.write_text(
        json.dumps(
            {
                "verdict": "needs-revision",
                "summary": "Has issues.",
                "unresolved_concerns": [
                    {"severity": "major", "concern": "x", "location": "general"}
                ],
                "addressed_in_this_iteration": [],
            }
        )
    )
    assert _review_verdict_schema(p) is None


def test_review_verdict_schema_passes_with_addressed(tmp_path: Path) -> None:
    p = tmp_path / "review.json"
    p.write_text(
        json.dumps({**_VALID_VERDICT, "addressed_in_this_iteration": ["fixed the naming"]})
    )
    assert _review_verdict_schema(p) is None


def test_review_verdict_schema_fails_invalid_verdict_value(tmp_path: Path) -> None:
    p = tmp_path / "review.json"
    p.write_text(json.dumps({**_VALID_VERDICT, "verdict": "maybe"}))
    assert _review_verdict_schema(p) is not None


def test_review_verdict_schema_fails_missing_summary(tmp_path: Path) -> None:
    data = {k: v for k, v in _VALID_VERDICT.items() if k != "summary"}
    p = tmp_path / "review.json"
    p.write_text(json.dumps(data))
    assert _review_verdict_schema(p) is not None


def test_review_verdict_schema_fails_missing_verdict_field(tmp_path: Path) -> None:
    data = {k: v for k, v in _VALID_VERDICT.items() if k != "verdict"}
    p = tmp_path / "review.json"
    p.write_text(json.dumps(data))
    assert _review_verdict_schema(p) is not None


def test_review_verdict_schema_fails_bad_concern_severity(tmp_path: Path) -> None:
    p = tmp_path / "review.json"
    p.write_text(
        json.dumps(
            {
                **_VALID_VERDICT,
                "verdict": "needs-revision",
                "unresolved_concerns": [
                    {"severity": "catastrophic", "concern": "x", "location": "general"}
                ],
            }
        )
    )
    assert _review_verdict_schema(p) is not None


def test_review_verdict_schema_fails_bad_json(tmp_path: Path) -> None:
    p = tmp_path / "review.json"
    p.write_text("{not json}")
    assert _review_verdict_schema(p) is not None


def test_review_verdict_schema_fails_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    assert _review_verdict_schema(p) is not None
