"""Tests for ``shared.models.hil`` — including discriminated unions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.models import (
    AskAnswer,
    AskQuestion,
    HilItem,
    ManualStepAnswer,
    ManualStepQuestion,
    ReviewAnswer,
    ReviewQuestion,
)
from tests.shared.factories import (
    make_ask_hil_item,
    make_manual_step_hil_item,
    make_review_hil_item,
)


def test_ask_factory_roundtrip() -> None:
    item = make_ask_hil_item()
    assert isinstance(item.question, AskQuestion)
    assert HilItem.model_validate_json(item.model_dump_json()) == item


def test_review_factory_roundtrip() -> None:
    item = make_review_hil_item()
    assert isinstance(item.question, ReviewQuestion)
    assert HilItem.model_validate_json(item.model_dump_json()) == item


def test_manual_step_factory_roundtrip() -> None:
    item = make_manual_step_hil_item()
    assert isinstance(item.question, ManualStepQuestion)
    assert HilItem.model_validate_json(item.model_dump_json()) == item


def test_discriminator_routes_to_correct_question_type() -> None:
    """An ``ask``-kind item with a Review question shape must be rejected."""
    with pytest.raises(ValidationError):
        HilItem.model_validate(
            {
                **make_ask_hil_item().model_dump(mode="json"),
                "question": {"kind": "review", "target": "x.md", "prompt": "p"},
            }
        )


def test_answer_attached_after_submit() -> None:
    item = make_ask_hil_item()
    answered = item.model_copy(
        update={
            "status": "answered",
            "answer": AskAnswer(text="Yes."),
            "answered_at": item.created_at,
        }
    )
    j = answered.model_dump_json()
    parsed = HilItem.model_validate_json(j)
    assert parsed.answer is not None
    assert isinstance(parsed.answer, AskAnswer)


def test_review_answer_decision_constrained() -> None:
    with pytest.raises(ValidationError):
        ReviewAnswer.model_validate({"decision": "maybe", "comments": "x"})


def test_invalid_status_rejected() -> None:
    with pytest.raises(ValidationError):
        HilItem.model_validate({**make_ask_hil_item().model_dump(mode="json"), "status": "weird"})


def test_manual_step_answer_minimal() -> None:
    a = ManualStepAnswer(output="done")
    assert a.kind == "manual-step"
