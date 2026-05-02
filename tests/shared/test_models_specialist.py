"""Tests for ``shared.models.specialist``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.models import (
    AgentDef,
    AgentEntry,
    MaterialisedSpawn,
    SkillDef,
    SkillEntry,
    SpecialistCatalogue,
)
from tests.shared.factories import make_agent_def, make_skill_def, make_specialist_catalogue


def test_agent_def_roundtrip() -> None:
    a = make_agent_def()
    assert AgentDef.model_validate_json(a.model_dump_json()) == a


def test_skill_def_roundtrip() -> None:
    s = make_skill_def()
    assert SkillDef.model_validate_json(s.model_dump_json()) == s


def test_specialist_catalogue_with_entries_roundtrip() -> None:
    cat = SpecialistCatalogue(
        project_slug="figur-backend-v2",
        agents=[
            AgentEntry(
                agent_ref="design-spec-writer",
                source="global",
                has_override_for_project=False,
                description="x",
                model="claude-opus-4-7",
            )
        ],
        skills=[
            SkillEntry(
                skill_id="markdown-spec",
                source="global",
                has_override_for_project=False,
                description="x",
                triggering_summary="x",
            )
        ],
    )
    assert SpecialistCatalogue.model_validate_json(cat.model_dump_json()) == cat


def test_agent_entry_invalid_source_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentEntry.model_validate(
            {
                "agent_ref": "x",
                "source": "global-default",  # not in literal
                "has_override_for_project": False,
                "description": "x",
                "model": "x",
            }
        )


def test_materialised_spawn_minimal() -> None:
    m = MaterialisedSpawn(agents_json="/tmp/agents.json", settings_path="/tmp/settings/")
    assert MaterialisedSpawn.model_validate_json(m.model_dump_json()) == m


def test_specialist_catalogue_factory() -> None:
    cat = make_specialist_catalogue()
    assert cat.agents == []
    assert cat.skills == []
