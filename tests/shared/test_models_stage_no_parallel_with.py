"""Regression test — `parallel_with` is removed from StageDefinition.

Per `docs/v0-alignment-report.md` Plan #4: the field was validated but never
honoured at runtime (the Job Driver iterates stages strictly sequentially).
A validated-but-unused field is a contract trap, so it is removed for v0
and re-introduced when a real template needs stage-level parallelism.

This test asserts that:
- The model rejects a `parallel_with` key in input data (Pydantic
  `extra="forbid"`).
- StageDefinition has no `parallel_with` attribute.

If a future stage needs parallel-stage dispatch, the design + scheduler
land together; this test will fail intentionally and the maintainer will
reintroduce the field with its enforcement.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    StageDefinition,
)


def _minimal_stage_kwargs() -> dict:
    return {
        "id": "s",
        "worker": "agent",
        "inputs": InputSpec(required=[], optional=None),
        "outputs": OutputSpec(required=[]),
        "budget": Budget(max_turns=5),
        "exit_condition": ExitCondition(required_outputs=None),
    }


def test_stage_definition_rejects_parallel_with_field() -> None:
    """Passing parallel_with (in any form) must raise ValidationError."""
    with pytest.raises(ValidationError) as exc:
        StageDefinition(parallel_with=["other-stage"], **_minimal_stage_kwargs())  # type: ignore[call-arg]
    # Pydantic v2 reports unknown keys as 'extra_forbidden'.
    assert "parallel_with" in str(exc.value)


def test_stage_definition_has_no_parallel_with_attribute() -> None:
    stage = StageDefinition(**_minimal_stage_kwargs())
    assert not hasattr(stage, "parallel_with")
