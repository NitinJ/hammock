"""Unit tests for shared/v1/envelope.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from shared.v1.envelope import (
    Envelope,
    EnvelopeMismatch,
    envelope_filename,
    expect,
    make_envelope,
)


# ---------------------------------------------------------------------------
# make_envelope
# ---------------------------------------------------------------------------


def test_make_envelope_minimal() -> None:
    env = make_envelope(
        type_name="bug-report",
        producer_node="write-bug-report",
        value_payload={"summary": "x"},
    )
    assert env.type == "bug-report"
    assert env.type_version == 1
    assert env.producer_node == "write-bug-report"
    assert env.value == {"summary": "x"}
    assert env.repo is None


def test_make_envelope_with_repo() -> None:
    env = make_envelope(
        type_name="pr",
        producer_node="implement",
        value_payload={"url": "https://example.com/pr/1"},
        repo="me/test-repo",
    )
    assert env.repo == "me/test-repo"


def test_make_envelope_default_produced_at_is_recent_utc() -> None:
    before = datetime.now(UTC)
    env = make_envelope(
        type_name="x", producer_node="n", value_payload={}
    )
    after = datetime.now(UTC)
    assert before <= env.produced_at <= after
    assert env.produced_at.tzinfo == UTC


def test_make_envelope_explicit_now() -> None:
    fixed = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    env = make_envelope(
        type_name="x", producer_node="n", value_payload={}, now=fixed
    )
    assert env.produced_at == fixed


def test_make_envelope_explicit_type_version() -> None:
    env = make_envelope(
        type_name="x", producer_node="n", value_payload={}, type_version=3
    )
    assert env.type_version == 3


# ---------------------------------------------------------------------------
# Envelope rejects extra fields
# ---------------------------------------------------------------------------


def test_envelope_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Envelope(  # type: ignore[call-arg]
            type="x",
            producer_node="n",
            produced_at=datetime.now(UTC),
            value={},
            unknown="x",
        )


# ---------------------------------------------------------------------------
# expect()
# ---------------------------------------------------------------------------


def test_expect_passes_for_matching_type_and_version() -> None:
    env = make_envelope(
        type_name="bug-report", producer_node="n", value_payload={}, type_version=1
    )
    expect(env, type_name="bug-report", type_version=1)  # does not raise


def test_expect_raises_on_type_mismatch() -> None:
    env = make_envelope(
        type_name="pr", producer_node="n", value_payload={}
    )
    with pytest.raises(EnvelopeMismatch, match="type mismatch"):
        expect(env, type_name="branch")


def test_expect_raises_on_version_mismatch() -> None:
    env = make_envelope(
        type_name="bug-report", producer_node="n", value_payload={}, type_version=1
    )
    with pytest.raises(EnvelopeMismatch, match="type_version mismatch"):
        expect(env, type_name="bug-report", type_version=2)


# ---------------------------------------------------------------------------
# Filename convention
# ---------------------------------------------------------------------------


def test_envelope_filename_for_simple_var() -> None:
    assert envelope_filename("bug_report") == "bug_report.json"


def test_envelope_filename_for_loop_indexed_var() -> None:
    assert envelope_filename("loop_implement_pr_5") == "loop_implement_pr_5.json"


# ---------------------------------------------------------------------------
# Round-trip through JSON
# ---------------------------------------------------------------------------


def test_envelope_round_trips_through_json() -> None:
    env = make_envelope(
        type_name="bug-report",
        producer_node="write-bug-report",
        value_payload={"summary": "the bug", "severity": "high"},
        repo="me/repo",
    )
    encoded = env.model_dump_json()
    parsed = Envelope.model_validate_json(encoded)
    assert parsed == env

    # Sanity-check that the JSON is human-readable too
    decoded = json.loads(encoded)
    assert decoded["type"] == "bug-report"
    assert decoded["value"]["summary"] == "the bug"
