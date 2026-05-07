"""Unit tests for engine/v1/predicate.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.v1.predicate import (
    PredicateError,
    evaluate,
    parse_predicate,
    parse_ref,
)
from shared.v1 import paths
from shared.v1.envelope import make_envelope
from shared.v1.workflow import Workflow


def _empty_workflow() -> Workflow:
    return Workflow.model_validate({"workflow": "t", "variables": {}, "nodes": []})


# ---------------------------------------------------------------------------
# parse_ref
# ---------------------------------------------------------------------------


def test_parse_ref_plain() -> None:
    r = parse_ref("$tests_pr")
    assert r.loop_id is None
    assert r.var_name == "tests_pr"
    assert r.index_form is None
    assert r.field_path == []


def test_parse_ref_plain_with_field() -> None:
    r = parse_ref("$report.verdict")
    assert r.var_name == "report"
    assert r.field_path == ["verdict"]


def test_parse_ref_loop_i() -> None:
    r = parse_ref("$pr-merged-loop.pr_review[i].verdict")
    assert r.loop_id == "pr-merged-loop"
    assert r.var_name == "pr_review"
    assert r.index_form == "i"
    assert r.field_path == ["verdict"]


def test_parse_ref_loop_iminus1() -> None:
    r = parse_ref("$L.x[i-1]")
    assert r.index_form == "i-1"


def test_parse_ref_loop_last() -> None:
    r = parse_ref("$L.x[last]")
    assert r.index_form == "last"


def test_parse_ref_loop_int_index() -> None:
    r = parse_ref("$L.x[3].field")
    assert r.index_form == "3"
    assert r.field_path == ["field"]


def test_parse_ref_malformed_raises() -> None:
    with pytest.raises(PredicateError):
        parse_ref("not-a-ref")


# ---------------------------------------------------------------------------
# parse_predicate
# ---------------------------------------------------------------------------


def test_parse_predicate_eq_string_literal() -> None:
    p = parse_predicate("$L.r[i].verdict == 'merged'")
    assert p.op == "=="
    assert p.literal == "merged"


def test_parse_predicate_neq_string() -> None:
    p = parse_predicate("$L.r[i].verdict != 'merged'")
    assert p.op == "!="


def test_parse_predicate_int_literal() -> None:
    p = parse_predicate("$L.r[i].count == 3")
    assert p.literal == 3


def test_parse_predicate_bool_literal() -> None:
    p = parse_predicate("$x.flag == true")
    assert p.literal is True


def test_parse_predicate_bare_reference() -> None:
    """`runs_if: $tests_pr` form — no operator, truthiness check."""
    p = parse_predicate("$tests_pr")
    assert p.op is None
    assert p.literal is None
    assert p.ref.var_name == "tests_pr"


# ---------------------------------------------------------------------------
# evaluate — plain reference, bare-reference truthiness
# ---------------------------------------------------------------------------


def _seed_plain_envelope(
    *, root: Path, job_slug: str, var_name: str, type_name: str, value: dict
) -> None:
    paths.ensure_job_layout(job_slug, root=root)
    env = make_envelope(type_name=type_name, producer_node="<test>", value_payload=value)
    paths.variable_envelope_path(job_slug, var_name, root=root).write_text(env.model_dump_json())


def test_evaluate_bare_ref_truthy_when_present(tmp_path: Path) -> None:
    _seed_plain_envelope(
        root=tmp_path,
        job_slug="j",
        var_name="tests_pr",
        type_name="pr",
        value={
            "url": "https://github.com/x/y/pull/1",
            "number": 1,
            "branch": "b",
            "base": "bb",
            "repo": "x/y",
        },
    )
    assert evaluate("$tests_pr", workflow=_empty_workflow(), job_slug="j", root=tmp_path) is True


def test_evaluate_bare_ref_falsy_when_absent(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    assert evaluate("$missing", workflow=_empty_workflow(), job_slug="j", root=tmp_path) is False


# ---------------------------------------------------------------------------
# evaluate — loop-scoped references
# ---------------------------------------------------------------------------


def _seed_loop_envelope(
    *,
    root: Path,
    job_slug: str,
    loop_id: str,
    var_name: str,
    iteration: int,
    type_name: str,
    value: dict,
) -> None:
    paths.ensure_job_layout(job_slug, root=root)
    env = make_envelope(type_name=type_name, producer_node="<test>", value_payload=value)
    paths.loop_variable_envelope_path(job_slug, loop_id, var_name, iteration, root=root).write_text(
        env.model_dump_json()
    )


def test_evaluate_loop_i_at_current_iteration(tmp_path: Path) -> None:
    _seed_loop_envelope(
        root=tmp_path,
        job_slug="j",
        loop_id="L",
        var_name="r",
        iteration=0,
        type_name="pr-review-verdict",
        value={
            "verdict": "merged",
            "summary": "ok",
        },
    )
    ok = evaluate(
        "$L.r[i].verdict == 'merged'",
        workflow=_empty_workflow(),
        job_slug="j",
        root=tmp_path,
        current_iteration=0,
    )
    assert ok is True


def test_evaluate_loop_i_minus_1_on_first_iter_returns_false(
    tmp_path: Path,
) -> None:
    """`[i-1]` on iter 0 has no prior — equality with anything is False
    (value is None)."""
    paths.ensure_job_layout("j", root=tmp_path)
    ok = evaluate(
        "$L.r[i-1].verdict == 'merged'",
        workflow=_empty_workflow(),
        job_slug="j",
        root=tmp_path,
        current_iteration=0,
    )
    assert ok is False


def test_evaluate_loop_last_finds_highest_iteration(tmp_path: Path) -> None:
    """`[last]` reads the highest iteration's envelope."""
    for i, v in enumerate(["needs-revision", "merged"]):
        _seed_loop_envelope(
            root=tmp_path,
            job_slug="j",
            loop_id="L",
            var_name="r",
            iteration=i,
            type_name="pr-review-verdict",
            value={
                "verdict": v,
                "summary": "x",
            },
        )
    ok = evaluate(
        "$L.r[last].verdict == 'merged'",
        workflow=_empty_workflow(),
        job_slug="j",
        root=tmp_path,
    )
    assert ok is True


def test_evaluate_neq_on_string(tmp_path: Path) -> None:
    _seed_loop_envelope(
        root=tmp_path,
        job_slug="j",
        loop_id="L",
        var_name="r",
        iteration=0,
        type_name="pr-review-verdict",
        value={
            "verdict": "needs-revision",
            "summary": "x",
        },
    )
    ok = evaluate(
        "$L.r[i].verdict != 'merged'",
        workflow=_empty_workflow(),
        job_slug="j",
        root=tmp_path,
        current_iteration=0,
    )
    assert ok is True


# ---------------------------------------------------------------------------
# evaluate — missing-field tolerance
# ---------------------------------------------------------------------------


def test_evaluate_missing_field_returns_falsy(tmp_path: Path) -> None:
    _seed_plain_envelope(
        root=tmp_path,
        job_slug="j",
        var_name="report",
        type_name="bug-report",
        value={"summary": "x", "document": "## Bug\n\nx"},
    )
    # `report.no_such_field` doesn't exist; should evaluate as if value None.
    assert (
        evaluate(
            "$report.no_such_field == 'x'",
            workflow=_empty_workflow(),
            job_slug="j",
            root=tmp_path,
        )
        is False
    )
