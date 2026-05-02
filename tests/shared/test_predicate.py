"""Tests for ``shared.predicate`` — the minimal predicate grammar."""

from __future__ import annotations

import pytest

from shared.predicate import PredicateError, evaluate_predicate, parse_predicate

# Happy-path samples mirroring design doc § Predicate grammar ---------------


def test_simple_equality_string() -> None:
    ctx = {"design-review": {"json": {"verdict": "approved"}}}
    assert evaluate_predicate("design-review.json.verdict == 'approved'", ctx) is True
    assert evaluate_predicate("design-review.json.verdict == 'rejected'", ctx) is False


def test_inequality() -> None:
    ctx = {"a": {"b": "x"}}
    assert evaluate_predicate("a.b != 'y'", ctx) is True
    assert evaluate_predicate("a.b != 'x'", ctx) is False


def test_boolean_literal() -> None:
    ctx = {"flag": True}
    assert evaluate_predicate("flag == true", ctx) is True
    assert evaluate_predicate("flag == false", ctx) is False


def test_logical_and_or() -> None:
    ctx = {"a": "x", "b": True}
    assert evaluate_predicate("a == 'x' and b == true", ctx) is True
    assert evaluate_predicate("a == 'x' and b == false", ctx) is False
    assert evaluate_predicate("a == 'x' or b == false", ctx) is True


def test_not_operator() -> None:
    ctx = {"verdict": "approved"}
    assert evaluate_predicate("not verdict == 'rejected'", ctx) is True
    assert evaluate_predicate("not (verdict == 'approved')", ctx) is False


def test_parens_grouping() -> None:
    ctx = {"a": "x", "b": "y", "c": "z"}
    assert evaluate_predicate("(a == 'x' or b == 'q') and c == 'z'", ctx) is True


def test_double_quoted_string() -> None:
    ctx = {"v": "hi"}
    assert evaluate_predicate('v == "hi"', ctx) is True


# Routing-decision shape ----------------------------------------------------


def test_review_verdict_predicate() -> None:
    """Canonical predicate from design doc: route on review verdict."""
    ctx = {"design-spec-review-agent": {"json": {"verdict": "needs-revision"}}}
    pred = "design-spec-review-agent.json.verdict != 'approved'"
    assert evaluate_predicate(pred, ctx) is True


# Error paths ---------------------------------------------------------------


def test_unknown_path_raises() -> None:
    with pytest.raises(PredicateError, match="not present"):
        evaluate_predicate("missing.field == 'x'", {})


def test_arithmetic_rejected() -> None:
    with pytest.raises(PredicateError):
        parse_predicate("a + b == 'x'")


def test_function_call_rejected() -> None:
    with pytest.raises(PredicateError):
        parse_predicate("count(a) == 1")


def test_unterminated_string_rejected() -> None:
    with pytest.raises(PredicateError, match="unterminated"):
        parse_predicate("v == 'foo")


def test_trailing_tokens_rejected() -> None:
    with pytest.raises(PredicateError, match="trailing"):
        parse_predicate("a == 'x' garbage")


def test_path_traversal_through_non_dict_raises() -> None:
    with pytest.raises(PredicateError, match="expected dict"):
        evaluate_predicate("a.b == 'x'", {"a": "scalar"})


def test_caching_via_pre_parsed_ast() -> None:
    ast = parse_predicate("v == 'ok'")
    assert evaluate_predicate(ast, {"v": "ok"}) is True
    assert evaluate_predicate(ast, {"v": "no"}) is False
