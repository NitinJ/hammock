"""Tests for ``shared.models.plan``."""

from __future__ import annotations

from shared.models import Plan, PlanStage, StageDefinition
from tests.shared.factories import make_stage_definition


def test_plan_alias_is_stage_definition() -> None:
    assert PlanStage is StageDefinition


def test_plan_roundtrip() -> None:
    p = Plan(stages=[make_stage_definition()])
    assert Plan.model_validate_json(p.model_dump_json()) == p


def test_empty_plan_valid() -> None:
    Plan()
