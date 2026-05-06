"""Unit tests for the v1 variable types.

One test module covers all four T1 types because the produce/render/form
contract is identical in shape; per-type customisations are minimal
in v1 (each is ~50 lines).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from shared.v1.types.bug_report import BugReportType, BugReportValue
from shared.v1.types.design_spec import DesignSpecType, DesignSpecValue
from shared.v1.types.job_request import JobRequestType, JobRequestValue
from shared.v1.types.protocol import VariableTypeError
from shared.v1.types.registry import (
    REGISTRY,
    UnknownVariableType,
    get_type,
    known_type_names,
)
from shared.v1.types.review_verdict import (
    ReviewVerdictType,
    ReviewVerdictValue,
)


@dataclass
class FakeNodeCtx:
    var_name: str
    job_dir: Path
    inputs: dict[str, object] = field(default_factory=dict)

    def expected_path(self) -> Path:
        return self.job_dir / f"{self.var_name}.json"


@dataclass
class FakePromptCtx:
    var_name: str
    job_dir: Path

    def expected_path(self) -> Path:
        return self.job_dir / "variables" / f"{self.var_name}.json"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_contains_v1_types() -> None:
    names = set(known_type_names())
    assert {"job-request", "bug-report", "design-spec", "review-verdict"}.issubset(names)


def test_get_type_returns_singleton_per_name() -> None:
    assert get_type("bug-report") is REGISTRY["bug-report"]


def test_get_type_unknown_raises() -> None:
    with pytest.raises(UnknownVariableType):
        get_type("nonexistent-type")


# ---------------------------------------------------------------------------
# job-request
# ---------------------------------------------------------------------------


def test_job_request_produce_raises_because_engine_owned() -> None:
    """job-request is engine-written at submit time. If `produce` is
    called for it post-actor, that's a wiring bug."""
    t = JobRequestType()
    ctx = FakeNodeCtx(var_name="request", job_dir=Path("/tmp"))
    with pytest.raises(VariableTypeError, match="engine-produced"):
        t.produce(t.Decl(), ctx)


def test_job_request_renders_for_consumer() -> None:
    t = JobRequestType()
    value = JobRequestValue(text="Fix the bug in add_integers when called with no args")
    ctx = FakePromptCtx(var_name="request", job_dir=Path("/tmp"))
    rendered = t.render_for_consumer(t.Decl(), value, ctx)
    assert "request" in rendered
    assert "Fix the bug" in rendered


# ---------------------------------------------------------------------------
# bug-report
# ---------------------------------------------------------------------------


def test_bug_report_produce_happy_path(tmp_path: Path) -> None:
    t = BugReportType()
    payload = {
        "summary": "add_integers returns None on empty",
        "repro_steps": ["Call add_integers()", "Inspect result"],
        "expected_behaviour": "0 (additive identity)",
        "actual_behaviour": "None",
    }
    (tmp_path / "bug_report.json").write_text(json.dumps(payload))
    ctx = FakeNodeCtx(var_name="bug_report", job_dir=tmp_path)
    value = t.produce(t.Decl(), ctx)
    assert isinstance(value, BugReportValue)
    assert value.summary == payload["summary"]
    assert len(value.repro_steps) == 2


def test_bug_report_produce_missing_file_raises(tmp_path: Path) -> None:
    t = BugReportType()
    ctx = FakeNodeCtx(var_name="bug_report", job_dir=tmp_path)
    with pytest.raises(VariableTypeError, match="not produced"):
        t.produce(t.Decl(), ctx)


def test_bug_report_produce_empty_file_raises(tmp_path: Path) -> None:
    t = BugReportType()
    (tmp_path / "bug_report.json").write_text("   ")
    ctx = FakeNodeCtx(var_name="bug_report", job_dir=tmp_path)
    with pytest.raises(VariableTypeError, match="empty"):
        t.produce(t.Decl(), ctx)


def test_bug_report_produce_invalid_json_raises(tmp_path: Path) -> None:
    t = BugReportType()
    (tmp_path / "bug_report.json").write_text("{ broken")
    ctx = FakeNodeCtx(var_name="bug_report", job_dir=tmp_path)
    with pytest.raises(VariableTypeError, match="not valid JSON"):
        t.produce(t.Decl(), ctx)


def test_bug_report_produce_extra_fields_rejected(tmp_path: Path) -> None:
    t = BugReportType()
    (tmp_path / "bug_report.json").write_text(json.dumps({"summary": "x", "extra": "no"}))
    ctx = FakeNodeCtx(var_name="bug_report", job_dir=tmp_path)
    with pytest.raises(VariableTypeError, match="schema invalid"):
        t.produce(t.Decl(), ctx)


def test_bug_report_render_for_producer_includes_path_and_schema_hint(
    tmp_path: Path,
) -> None:
    t = BugReportType()
    ctx = FakePromptCtx(var_name="bug_report", job_dir=tmp_path)
    rendered = t.render_for_producer(t.Decl(), ctx)
    assert "bug_report.json" in rendered
    assert "summary" in rendered  # schema field hint
    assert "extra='forbid'" in rendered


def test_bug_report_render_for_consumer_includes_summary() -> None:
    t = BugReportType()
    value = BugReportValue(summary="the summary line", repro_steps=["a", "b"])
    ctx = FakePromptCtx(var_name="bug_report", job_dir=Path("/tmp"))
    rendered = t.render_for_consumer(t.Decl(), value, ctx)
    assert "the summary line" in rendered
    assert "Repro steps" in rendered


# ---------------------------------------------------------------------------
# design-spec
# ---------------------------------------------------------------------------


def test_design_spec_produce_happy_path(tmp_path: Path) -> None:
    t = DesignSpecType()
    payload = {
        "title": "Fix empty-args sum",
        "overview": "Make add_integers return 0 instead of None.",
        "proposed_changes": ["Drop the `not nums` guard"],
        "risks": [],
        "out_of_scope": ["Type signature changes beyond removing | None"],
    }
    (tmp_path / "design_spec.json").write_text(json.dumps(payload))
    ctx = FakeNodeCtx(var_name="design_spec", job_dir=tmp_path)
    value = t.produce(t.Decl(), ctx)
    assert isinstance(value, DesignSpecValue)
    assert value.title == "Fix empty-args sum"


def test_design_spec_render_for_consumer_lists_changes() -> None:
    t = DesignSpecType()
    value = DesignSpecValue(
        title="t",
        overview="ov",
        proposed_changes=["change A", "change B"],
    )
    ctx = FakePromptCtx(var_name="design_spec", job_dir=Path("/tmp"))
    rendered = t.render_for_consumer(t.Decl(), value, ctx)
    assert "change A" in rendered and "change B" in rendered


# ---------------------------------------------------------------------------
# review-verdict
# ---------------------------------------------------------------------------


def test_review_verdict_produce_happy_path(tmp_path: Path) -> None:
    t = ReviewVerdictType()
    payload = {"verdict": "approved", "summary": "looks good"}
    (tmp_path / "verdict.json").write_text(json.dumps(payload))
    ctx = FakeNodeCtx(var_name="verdict", job_dir=tmp_path)
    value = t.produce(t.Decl(), ctx)
    assert isinstance(value, ReviewVerdictValue)
    assert value.verdict == "approved"


def test_review_verdict_produce_needs_revision(tmp_path: Path) -> None:
    t = ReviewVerdictType()
    payload = {"verdict": "needs-revision", "summary": "rework section 3"}
    (tmp_path / "verdict.json").write_text(json.dumps(payload))
    ctx = FakeNodeCtx(var_name="verdict", job_dir=tmp_path)
    value = t.produce(t.Decl(), ctx)
    assert value.verdict == "needs-revision"
    assert value.summary == "rework section 3"


def test_review_verdict_produce_invalid_verdict_rejected(tmp_path: Path) -> None:
    t = ReviewVerdictType()
    (tmp_path / "verdict.json").write_text(
        json.dumps({"verdict": "kinda-approved", "summary": "x"})
    )
    ctx = FakeNodeCtx(var_name="verdict", job_dir=tmp_path)
    with pytest.raises(VariableTypeError, match="schema invalid"):
        t.produce(t.Decl(), ctx)


def test_review_verdict_rejects_obsolete_fields(tmp_path: Path) -> None:
    """Stage 2 simplification: unresolved_concerns / addressed_in_this_iteration
    no longer accepted (extra='forbid')."""
    t = ReviewVerdictType()
    (tmp_path / "verdict.json").write_text(
        json.dumps(
            {
                "verdict": "approved",
                "summary": "x",
                "unresolved_concerns": [],
            }
        )
    )
    ctx = FakeNodeCtx(var_name="verdict", job_dir=tmp_path)
    with pytest.raises(VariableTypeError, match="schema invalid"):
        t.produce(t.Decl(), ctx)


def test_review_verdict_rejects_merged_verdict(tmp_path: Path) -> None:
    """Stage 2: 'merged' moved to pr-review-verdict; review-verdict no
    longer accepts it."""
    t = ReviewVerdictType()
    (tmp_path / "verdict.json").write_text(json.dumps({"verdict": "merged", "summary": "x"}))
    ctx = FakeNodeCtx(var_name="verdict", job_dir=tmp_path)
    with pytest.raises(VariableTypeError, match="schema invalid"):
        t.produce(t.Decl(), ctx)


def test_review_verdict_render_for_consumer_minimal() -> None:
    t = ReviewVerdictType()
    value = ReviewVerdictValue(verdict="needs-revision", summary="please fix")
    ctx = FakePromptCtx(var_name="verdict", job_dir=Path("/tmp"))
    rendered = t.render_for_consumer(t.Decl(), value, ctx)
    assert "needs-revision" in rendered
    assert "please fix" in rendered


def test_review_verdict_form_schema_defined() -> None:
    """ReviewVerdict is human-producible, so `form_schema` must return a
    non-None FormSchema (the dashboard renders the gate UI from it)."""
    t = ReviewVerdictType()
    schema = t.form_schema(t.Decl())
    assert schema is not None
    field_names = [name for name, _ in schema.fields]
    assert field_names == ["verdict", "summary"]
