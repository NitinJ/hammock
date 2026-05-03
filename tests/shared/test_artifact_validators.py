"""Tests for ``shared.artifact_validators``."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from shared.artifact_validators import (
    REGISTRY,
    _integration_test_report_schema,
    _non_empty,
    _plan_schema,
    _review_verdict_schema,
)


def test_registry_contains_all_expected_names() -> None:
    assert "non-empty" in REGISTRY
    assert "review-verdict-schema" in REGISTRY
    assert "plan-schema" in REGISTRY
    assert "integration-test-report-schema" in REGISTRY


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


# ---------------------------------------------------------------------------
# plan-schema
# ---------------------------------------------------------------------------

_VALID_PLAN_STAGE = {
    "id": "write",
    "worker": "agent",
    "agent_ref": "writer",
    "inputs": {"required": ["prompt.md"]},
    "outputs": {"required": ["spec.md"]},
    "budget": {"max_turns": 10},
    "exit_condition": {},
}


def test_plan_schema_passes_valid(tmp_path: Path) -> None:
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump({"stages": [_VALID_PLAN_STAGE]}))
    assert _plan_schema(p) is None


def test_plan_schema_fails_missing_stages_key(tmp_path: Path) -> None:
    p = tmp_path / "plan.yaml"
    p.write_text(yaml.safe_dump({"not_stages": []}))
    assert _plan_schema(p) is not None


def test_plan_schema_fails_bad_yaml(tmp_path: Path) -> None:
    p = tmp_path / "plan.yaml"
    p.write_text("{: bad yaml ][")
    assert _plan_schema(p) is not None


def test_plan_schema_fails_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.yaml"
    assert _plan_schema(p) is not None


# ---------------------------------------------------------------------------
# integration-test-report-schema
# ---------------------------------------------------------------------------

_VALID_REPORT = {
    "verdict": "passed",
    "summary": "All tests passed.",
    "test_command": "pytest tests/",
    "total_count": 3,
    "passed_count": 3,
    "failed_count": 0,
    "skipped_count": 0,
    "failures": [],
    "duration_seconds": 1.5,
}


def test_integration_test_report_schema_passes_valid(tmp_path: Path) -> None:
    p = tmp_path / "report.json"
    p.write_text(json.dumps(_VALID_REPORT))
    assert _integration_test_report_schema(p) is None


def test_integration_test_report_schema_passes_failed_verdict(tmp_path: Path) -> None:
    p = tmp_path / "report.json"
    p.write_text(
        json.dumps(
            {
                **_VALID_REPORT,
                "verdict": "failed",
                "passed_count": 2,
                "failed_count": 1,
                "failures": [
                    {
                        "test_name": "test_foo",
                        "file_path": "tests/test_foo.py",
                        "error_summary": "AssertionError",
                    }
                ],
            }
        )
    )
    assert _integration_test_report_schema(p) is None


def test_integration_test_report_schema_fails_invalid_verdict(tmp_path: Path) -> None:
    p = tmp_path / "report.json"
    p.write_text(json.dumps({**_VALID_REPORT, "verdict": "unknown"}))
    assert _integration_test_report_schema(p) is not None


def test_integration_test_report_schema_fails_count_mismatch(tmp_path: Path) -> None:
    p = tmp_path / "report.json"
    p.write_text(json.dumps({**_VALID_REPORT, "total_count": 99}))
    assert _integration_test_report_schema(p) is not None


def test_integration_test_report_schema_fails_verdict_count_mismatch(tmp_path: Path) -> None:
    p = tmp_path / "report.json"
    p.write_text(
        json.dumps({**_VALID_REPORT, "verdict": "passed", "failed_count": 1, "passed_count": 2})
    )
    assert _integration_test_report_schema(p) is not None


def test_integration_test_report_schema_fails_bad_json(tmp_path: Path) -> None:
    p = tmp_path / "report.json"
    p.write_text("{not json}")
    assert _integration_test_report_schema(p) is not None


def test_integration_test_report_schema_fails_missing_file(tmp_path: Path) -> None:
    p = tmp_path / "missing.json"
    assert _integration_test_report_schema(p) is not None
