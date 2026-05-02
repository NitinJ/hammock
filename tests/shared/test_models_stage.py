"""Tests for ``shared.models.stage``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.models import (
    Budget,
    LoopBack,
    OnExhaustion,
    StageDefinition,
    StageRun,
    StageState,
)
from tests.shared.factories import make_stage_definition, make_stage_run


def test_stage_definition_factory() -> None:
    s = make_stage_definition()
    assert s.id == "design"
    assert s.worker == "agent"


def test_stage_definition_roundtrip() -> None:
    s = make_stage_definition()
    assert StageDefinition.model_validate_json(s.model_dump_json()) == s


def test_stage_state_values() -> None:
    assert {s.value for s in StageState} == {
        "PENDING",
        "READY",
        "RUNNING",
        "PARTIALLY_BLOCKED",
        "BLOCKED_ON_HUMAN",
        "ATTENTION_NEEDED",
        "WRAPPING_UP",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
    }


def test_stage_run_roundtrip() -> None:
    r = make_stage_run()
    assert StageRun.model_validate_json(r.model_dump_json()) == r


def test_stage_run_attempt_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        StageRun(stage_id="x", attempt=0, state=StageState.PENDING)


def test_budget_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Budget(max_turns=-1)


def test_loop_back_factory() -> None:
    lb = LoopBack(
        to="design",
        condition="design-spec-review-agent.json.verdict != 'approved'",
        max_iterations=3,
        on_exhaustion=OnExhaustion(
            kind="hil-manual-step",
            prompt="Loop budget exhausted; please intervene.",
        ),
    )
    assert lb.max_iterations == 3
    assert LoopBack.model_validate_json(lb.model_dump_json()) == lb


def test_loop_back_zero_iterations_rejected() -> None:
    with pytest.raises(ValidationError):
        LoopBack(
            to="design",
            condition="x == 'y'",
            max_iterations=0,
            on_exhaustion=OnExhaustion(kind="hil-manual-step", prompt="x"),
        )


def test_invalid_worker_rejected() -> None:
    with pytest.raises(ValidationError):
        StageDefinition.model_validate(
            {**make_stage_definition().model_dump(mode="json"), "worker": "robot"}
        )
