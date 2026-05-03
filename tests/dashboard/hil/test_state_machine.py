"""Unit tests for the HIL state machine — pure transition logic."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dashboard.hil.state_machine import InvalidTransitionError, transition
from shared.models.hil import AskAnswer, AskQuestion, HilItem


def _item(status: str) -> HilItem:
    return HilItem(
        id="hil-1",
        kind="ask",
        stage_id="s1",
        created_at=datetime(2026, 5, 1, tzinfo=UTC),
        status=status,  # type: ignore[arg-type]
        question=AskQuestion(text="question?"),
        answer=AskAnswer(text="answer", choice=None) if status == "answered" else None,
    )


def test_awaiting_to_answered() -> None:
    item = _item("awaiting")
    updated = transition(item, "answered")
    assert updated.status == "answered"


def test_awaiting_to_cancelled() -> None:
    item = _item("awaiting")
    updated = transition(item, "cancelled")
    assert updated.status == "cancelled"


def test_answered_is_terminal() -> None:
    item = _item("answered")
    with pytest.raises(InvalidTransitionError):
        transition(item, "cancelled")


def test_cancelled_is_terminal() -> None:
    item = _item("cancelled")
    with pytest.raises(InvalidTransitionError):
        transition(item, "answered")


def test_awaiting_self_transition_raises() -> None:
    item = _item("awaiting")
    with pytest.raises(InvalidTransitionError):
        transition(item, "awaiting")


def test_transition_returns_new_object() -> None:
    item = _item("awaiting")
    updated = transition(item, "answered")
    assert updated is not item
    assert item.status == "awaiting"


def test_transition_preserves_other_fields() -> None:
    item = _item("awaiting")
    updated = transition(item, "cancelled")
    assert updated.id == item.id
    assert updated.kind == item.kind
    assert updated.stage_id == item.stage_id
    assert updated.question == item.question
