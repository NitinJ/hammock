"""Tests for ``shared.models.events``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.models import Event
from shared.models.events import EVENT_TYPES
from tests.shared.factories import make_event


def test_event_factory_roundtrip() -> None:
    e = make_event()
    assert Event.model_validate_json(e.model_dump_json()) == e


def test_event_seq_must_be_non_negative() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate({**make_event().model_dump(mode="json"), "seq": -1})


def test_event_invalid_source_rejected() -> None:
    with pytest.raises(ValidationError):
        Event.model_validate({**make_event().model_dump(mode="json"), "source": "xyz"})


def test_event_types_taxonomy_locked() -> None:
    """A representative cross-section of canonical types must be present."""
    must_have = {
        "job_state_transition",
        "stage_state_transition",
        "task_state_transition",
        "tool_invoked",
        "hil_item_opened",
        "cost_accrued",
    }
    assert must_have <= EVENT_TYPES
