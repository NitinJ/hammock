"""Stage primitive schemas.

Per design doc § Stage as the universal primitive. A stage is a typed
transformation ``(inputs) → outputs`` with side effects, observability, and
optional looping.

This file holds:
- ``StageDefinition`` — the static shape (declared in templates/plan.yaml)
- ``StageRun``        — per-run state (persisted to ``stages/<id>/run-N/``)
- ``StageState``      — the stage-level state machine
- supporting types: ``Budget``, ``ExitCondition``, ``LoopBack``, etc.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from shared.models.presentation import PresentationBlock


class StageState(StrEnum):
    """Stage-level state machine.

    Stages start ``PENDING``, become ``READY`` when their inputs exist, then
    transition through running states based on session liveness + task states.
    """

    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    PARTIALLY_BLOCKED = "PARTIALLY_BLOCKED"
    BLOCKED_ON_HUMAN = "BLOCKED_ON_HUMAN"
    ATTENTION_NEEDED = "ATTENTION_NEEDED"
    WRAPPING_UP = "WRAPPING_UP"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# StageDefinition support types
# ---------------------------------------------------------------------------


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required: list[str] = Field(default_factory=list)
    optional: list[str] | None = None


class OutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required: list[str] = Field(default_factory=list)


class Budget(BaseModel):
    """Hard budget caps per stage run. At least one cap must be set."""

    model_config = ConfigDict(extra="forbid")

    max_turns: int | None = Field(default=None, gt=0)
    max_budget_usd: float | None = Field(default=None, gt=0)
    max_wall_clock_min: int | None = Field(default=None, gt=0)


class RequiredOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    validators: list[str] | None = None


class ArtifactValidator(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    path: str = Field(min_length=1)
    schema_: str = Field(alias="schema", min_length=1)


class ExitCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    required_outputs: list[RequiredOutput] | None = None
    artifact_validators: list[ArtifactValidator] | None = None


class OnExhaustion(BaseModel):
    """What happens when ``loop_back.max_iterations`` is exceeded."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["hil-manual-step"]
    prompt: str = Field(min_length=1)


class LoopBack(BaseModel):
    """Iteration block on a verdict-producing stage.

    The orchestrator re-enters stage ``to`` when ``condition`` evaluates true,
    bounded by ``max_iterations``. The counter is keyed by
    ``(stage_id, loop_back.to)``.
    """

    model_config = ConfigDict(extra="forbid")

    to: str = Field(min_length=1, description="earlier stage_id to re-enter")
    condition: str = Field(min_length=1, description="predicate over outputs")
    max_iterations: int = Field(gt=0)
    on_exhaustion: OnExhaustion


# ---------------------------------------------------------------------------
# StageDefinition + StageRun
# ---------------------------------------------------------------------------


class StageDefinition(BaseModel):
    """Static stage shape — declared in templates and ``stage-list.yaml``."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    description: str | None = None
    worker: Literal["agent", "human"]
    agent_ref: str | None = None
    agent_config_overrides: dict[str, Any] | None = None

    inputs: InputSpec = Field(default_factory=InputSpec)
    outputs: OutputSpec = Field(default_factory=OutputSpec)

    budget: Budget
    exit_condition: ExitCondition

    runs_if: str | None = Field(
        default=None,
        description="optional predicate; when does this stage run?",
    )
    loop_back: LoopBack | None = None

    presentation: PresentationBlock | None = None
    parallel_with: list[str] | None = None
    is_expander: bool = False


class StageRun(BaseModel):
    """Per-run state; persisted to ``stages/<stage_id>/stage.json``.

    Per-attempt artefacts are at ``stages/<stage_id>/run-<attempt>/``;
    ``latest`` is a symlink to the current run.
    """

    model_config = ConfigDict(extra="forbid")

    stage_id: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    state: StageState

    started_at: datetime | None = None
    ended_at: datetime | None = None

    inputs_seen: list[str] = Field(default_factory=list)
    outputs_produced: list[str] = Field(default_factory=list)

    cli_session_id: str | None = None
    cost_accrued: float = 0.0
    restart_count: int = 0
