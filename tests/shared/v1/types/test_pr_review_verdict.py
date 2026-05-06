"""Stage 2 Step 1 — failing tests for pr-review-verdict.

Tests describe the contract for the new ``pr-review-verdict`` type per
design-patch §9.4 / impl-patch §Stage 2:

- Human submits ONLY ``{verdict: "merged" | "needs-revision"}``.
- Engine reads upstream ``pr`` from ``ctx.inputs["pr"]`` to get the URL.
- On ``merged``: ``gh pr view <url> --json state`` must return MERGED;
  otherwise reject. ``value.summary`` is empty (or short confirmation).
- On ``needs-revision``: ``gh pr view <url> --json
  comments,reviews,statusCheckRollup`` is fetched and aggregated into
  ``value.summary`` as prose for the next implement iteration.

Tests will fail at Step 1 (NotImplementedError) and drive Step 2.
Frozen for Step 3 — the Methodology forbids editing during the fix loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from shared.v1.types.pr_review_verdict import (
    PRReviewVerdictType,
    PRReviewVerdictValue,
)
from shared.v1.types.protocol import VariableTypeError


@dataclass
class FakeNodeCtx:
    var_name: str
    job_dir: Path
    inputs: dict[str, Any] = field(default_factory=dict)

    def expected_path(self) -> Path:
        return self.job_dir / f"{self.var_name}.json"


@dataclass
class FakePromptCtx:
    var_name: str
    job_dir: Path

    def expected_path(self) -> Path:
        return self.job_dir / f"{self.var_name}.json"


@dataclass
class _SubprocessResult:
    returncode: int
    stdout: str
    stderr: str = ""


# ---------------------------------------------------------------------------
# Decl + Value shape
# ---------------------------------------------------------------------------


def test_value_accepts_merged_with_empty_summary() -> None:
    v = PRReviewVerdictValue(verdict="merged", summary="")
    assert v.verdict == "merged"
    assert v.summary == ""


def test_value_accepts_needs_revision_with_summary() -> None:
    v = PRReviewVerdictValue(verdict="needs-revision", summary="reviewer: please fix X")
    assert v.verdict == "needs-revision"
    assert v.summary == "reviewer: please fix X"


def test_value_rejects_other_verdicts() -> None:
    """Only merged | needs-revision are accepted (not approved, rejected,
    or anything else)."""
    from pydantic import ValidationError

    for bad in ("approved", "rejected", "kinda-merged", ""):
        with pytest.raises(ValidationError):
            PRReviewVerdictValue.model_validate({"verdict": bad, "summary": ""})


def test_value_rejects_extra_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PRReviewVerdictValue.model_validate(
            {"verdict": "merged", "summary": "", "pr_url": "https://example.com/x"}
        )


# ---------------------------------------------------------------------------
# form_schema
# ---------------------------------------------------------------------------


def test_form_schema_is_two_button_shape() -> None:
    """No textarea — design §9.4: human picks one of two buttons; engine
    populates summary from gh."""
    t = PRReviewVerdictType()
    schema = t.form_schema(t.Decl())
    assert schema is not None
    assert schema.fields == [("verdict", "select:merged,needs-revision")]


# ---------------------------------------------------------------------------
# produce — happy path: merged
# ---------------------------------------------------------------------------


def _seed_submission(tmp_path: Path, payload: dict[str, Any]) -> None:
    """Write the human's submission to the expected path."""
    (tmp_path / "verdict.json").write_text(json.dumps(payload))


def test_produce_on_merged_verifies_via_gh(tmp_path: Path) -> None:
    """Human submitted {verdict: merged}. Engine queries gh pr view to
    confirm MERGED state, then accepts."""
    t = PRReviewVerdictType()
    _seed_submission(tmp_path, {"verdict": "merged"})

    ctx = FakeNodeCtx(
        var_name="verdict",
        job_dir=tmp_path,
        inputs={"pr": _PrInputStub("https://github.com/x/r/pull/42")},
    )

    with patch("shared.v1.types.pr_review_verdict.subprocess.run") as run_mock:
        run_mock.return_value = _SubprocessResult(returncode=0, stdout="MERGED")
        value = t.produce(t.Decl(), ctx)

    assert isinstance(value, PRReviewVerdictValue)
    assert value.verdict == "merged"
    # On merged, summary is empty or short confirmation. Either way: not
    # the full review aggregator output (which is needs-revision territory).
    assert value.summary == "" or "merged" in value.summary.lower()


def test_produce_on_merged_rejects_non_merged_state(tmp_path: Path) -> None:
    """If gh reports the PR is OPEN / CLOSED, the submission is rejected
    so the human merges it for real and re-submits."""
    t = PRReviewVerdictType()
    _seed_submission(tmp_path, {"verdict": "merged"})

    ctx = FakeNodeCtx(
        var_name="verdict",
        job_dir=tmp_path,
        inputs={"pr": _PrInputStub("https://github.com/x/r/pull/42")},
    )

    with patch("shared.v1.types.pr_review_verdict.subprocess.run") as run_mock:
        run_mock.return_value = _SubprocessResult(returncode=0, stdout="OPEN")
        with pytest.raises(VariableTypeError, match=r"not MERGED|merged"):
            t.produce(t.Decl(), ctx)


def test_produce_rejects_when_gh_fails(tmp_path: Path) -> None:
    t = PRReviewVerdictType()
    _seed_submission(tmp_path, {"verdict": "merged"})

    ctx = FakeNodeCtx(
        var_name="verdict",
        job_dir=tmp_path,
        inputs={"pr": _PrInputStub("https://github.com/x/r/pull/42")},
    )

    with patch("shared.v1.types.pr_review_verdict.subprocess.run") as run_mock:
        run_mock.return_value = _SubprocessResult(returncode=1, stdout="", stderr="gh: not found")
        with pytest.raises(VariableTypeError, match=r"gh|verify"):
            t.produce(t.Decl(), ctx)


# ---------------------------------------------------------------------------
# produce — happy path: needs-revision
# ---------------------------------------------------------------------------


_GH_NEEDS_REVISION_OUTPUT = json.dumps(
    {
        "comments": [
            {"author": {"login": "alice"}, "body": "this section is unclear"},
        ],
        "reviews": [
            {
                "author": {"login": "bob"},
                "state": "CHANGES_REQUESTED",
                "body": "please rework the validation",
            }
        ],
        "statusCheckRollup": [
            {"name": "ci", "conclusion": "FAILURE"},
        ],
    }
)


def test_produce_on_needs_revision_aggregates_gh_output(tmp_path: Path) -> None:
    """needs-revision: engine fetches comments + reviews + checks and
    aggregates into ``value.summary`` as prose."""
    t = PRReviewVerdictType()
    _seed_submission(tmp_path, {"verdict": "needs-revision"})

    ctx = FakeNodeCtx(
        var_name="verdict",
        job_dir=tmp_path,
        inputs={"pr": _PrInputStub("https://github.com/x/r/pull/42")},
    )

    with patch("shared.v1.types.pr_review_verdict.subprocess.run") as run_mock:
        run_mock.return_value = _SubprocessResult(returncode=0, stdout=_GH_NEEDS_REVISION_OUTPUT)
        value = t.produce(t.Decl(), ctx)

    assert value.verdict == "needs-revision"
    assert "alice" in value.summary
    assert "this section is unclear" in value.summary
    assert "bob" in value.summary
    assert "please rework the validation" in value.summary
    assert "ci" in value.summary.lower() or "FAILURE" in value.summary


def test_produce_on_needs_revision_when_no_feedback_still_succeeds(tmp_path: Path) -> None:
    """The human picked needs-revision but there are no comments / reviews /
    failing checks yet (e.g., the human is leaving feedback themselves
    after this submission). Engine accepts; summary may be empty or a
    placeholder."""
    t = PRReviewVerdictType()
    _seed_submission(tmp_path, {"verdict": "needs-revision"})

    ctx = FakeNodeCtx(
        var_name="verdict",
        job_dir=tmp_path,
        inputs={"pr": _PrInputStub("https://github.com/x/r/pull/42")},
    )

    empty = json.dumps({"comments": [], "reviews": [], "statusCheckRollup": []})
    with patch("shared.v1.types.pr_review_verdict.subprocess.run") as run_mock:
        run_mock.return_value = _SubprocessResult(returncode=0, stdout=empty)
        value = t.produce(t.Decl(), ctx)

    assert value.verdict == "needs-revision"
    # Either empty or a short "no feedback yet" indicator — implementation
    # choice. Just must not crash.
    assert isinstance(value.summary, str)


# ---------------------------------------------------------------------------
# produce — input wiring
# ---------------------------------------------------------------------------


def test_produce_requires_pr_input(tmp_path: Path) -> None:
    """If ctx.inputs has no "pr", produce raises — there's no URL to verify."""
    t = PRReviewVerdictType()
    _seed_submission(tmp_path, {"verdict": "merged"})

    ctx = FakeNodeCtx(var_name="verdict", job_dir=tmp_path, inputs={})

    with pytest.raises(VariableTypeError, match="pr"):
        t.produce(t.Decl(), ctx)


def test_produce_rejects_invalid_submission_payload(tmp_path: Path) -> None:
    """Submission with extra fields or missing verdict is rejected
    before any gh call."""
    t = PRReviewVerdictType()
    _seed_submission(tmp_path, {"summary": "look ma no verdict"})

    ctx = FakeNodeCtx(
        var_name="verdict",
        job_dir=tmp_path,
        inputs={"pr": _PrInputStub("https://github.com/x/r/pull/42")},
    )

    with pytest.raises(VariableTypeError):
        t.produce(t.Decl(), ctx)


# ---------------------------------------------------------------------------
# render_for_consumer
# ---------------------------------------------------------------------------


def test_render_for_consumer_includes_verdict_and_summary() -> None:
    t = PRReviewVerdictType()
    value = PRReviewVerdictValue(verdict="needs-revision", summary="bob: please fix")
    ctx = FakePromptCtx(var_name="pr_review", job_dir=Path("/tmp"))
    rendered = t.render_for_consumer(t.Decl(), value, ctx)
    assert "needs-revision" in rendered
    assert "bob: please fix" in rendered


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _PrInputStub:
    """Minimal stand-in for the upstream pr value as it appears in
    ctx.inputs["pr"]. Real engine populates this from the resolver as
    a PRValue Pydantic instance; the test only needs ``.url``."""

    url: str
