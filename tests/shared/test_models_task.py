"""Tests for ``shared.models.task``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.models import TaskRecord, TaskState
from tests.shared.factories import make_task_record


def test_task_state_values() -> None:
    assert {s.value for s in TaskState} == {
        "RUNNING",
        "BLOCKED_ON_HUMAN",
        "STUCK",
        "FAILED",
        "DONE",
        "CANCELLED",
    }


def test_task_factory() -> None:
    t = make_task_record()
    assert t.state is TaskState.RUNNING


def test_task_roundtrip() -> None:
    t = make_task_record()
    assert TaskRecord.model_validate_json(t.model_dump_json()) == t


def test_task_invalid_state_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskRecord.model_validate({**make_task_record().model_dump(mode="json"), "state": "BOGUS"})
