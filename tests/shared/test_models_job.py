"""Tests for ``shared.models.job``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from shared.models import (
    AgentCostSummary,
    JobConfig,
    JobCostSummary,
    JobState,
    StageCostSummary,
)
from tests.shared.factories import make_job


def test_job_factory() -> None:
    j = make_job()
    assert j.state is JobState.SUBMITTED


def test_job_state_values() -> None:
    """Lock the six canonical states from the design doc."""
    assert {s.value for s in JobState} == {
        "SUBMITTED",
        "STAGES_RUNNING",
        "BLOCKED_ON_HUMAN",
        "COMPLETED",
        "ABANDONED",
        "FAILED",
    }


def test_job_roundtrip() -> None:
    j = make_job()
    assert JobConfig.model_validate_json(j.model_dump_json()) == j


def test_invalid_state_rejected() -> None:
    with pytest.raises(ValidationError):
        JobConfig.model_validate({**make_job().model_dump(mode="json"), "state": "BOGUS"})


def test_cost_summary_roundtrip() -> None:
    summary = JobCostSummary(
        job_id="job-1",
        project_slug="figur-backend-v2",
        total_usd=4.21,
        total_tokens=12_000,
        by_stage={
            "design": StageCostSummary(
                stage_id="design",
                agent_ref="design-spec-writer",
                runs=1,
                total_usd=4.21,
                total_tokens=12_000,
            )
        },
        by_agent={
            "design-spec-writer": AgentCostSummary(
                agent_ref="design-spec-writer",
                invocations=1,
                total_usd=4.21,
                total_tokens=12_000,
            )
        },
        completed_at=datetime(2026, 5, 2, tzinfo=UTC),
    )
    assert JobCostSummary.model_validate_json(summary.model_dump_json()) == summary


def test_cost_summary_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        StageCostSummary(stage_id="x", agent_ref="a", runs=-1, total_usd=0.0, total_tokens=0)
