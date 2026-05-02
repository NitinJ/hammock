"""Job-level schemas.

Per design doc § Lifecycle § Job state machine and § Accounting Ledger.

A job is the unit the human submits; the Job Driver subprocess executes its
``stage-list.yaml`` deterministically. Six states; cost roll-ups computed
from ``cost_accrued`` events.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class JobState(StrEnum):
    """Job-level state machine — six states."""

    SUBMITTED = "SUBMITTED"
    STAGES_RUNNING = "STAGES_RUNNING"
    BLOCKED_ON_HUMAN = "BLOCKED_ON_HUMAN"
    COMPLETED = "COMPLETED"
    ABANDONED = "ABANDONED"
    FAILED = "FAILED"


class JobConfig(BaseModel):
    """Persisted to ``jobs/<job_slug>/job.json``."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    job_slug: str = Field(min_length=1)
    project_slug: str = Field(min_length=1)
    job_type: str = Field(min_length=1, description="e.g. 'build-feature', 'fix-bug'")
    created_at: datetime
    created_by: str = Field(min_length=1, description="username or 'human'")
    state: JobState


# ---------------------------------------------------------------------------
# Cost summaries (Accounting Ledger)
# ---------------------------------------------------------------------------


class StageCostSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_id: str = Field(min_length=1)
    agent_ref: str
    runs: int = Field(ge=0)
    total_usd: float = Field(ge=0)
    total_tokens: int = Field(ge=0)
    by_subagent: dict[str, float] = Field(default_factory=dict)


class AgentCostSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_ref: str = Field(min_length=1)
    invocations: int = Field(ge=0)
    total_usd: float = Field(ge=0)
    total_tokens: int = Field(ge=0)


class JobCostSummary(BaseModel):
    """Computed from ``cost_accrued`` events; persisted to ``job-summary.json``."""

    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)
    project_slug: str = Field(min_length=1)
    total_usd: float = Field(ge=0)
    total_tokens: int = Field(ge=0)
    by_stage: dict[str, StageCostSummary] = Field(default_factory=dict)
    by_agent: dict[str, AgentCostSummary] = Field(default_factory=dict)
    completed_at: datetime
