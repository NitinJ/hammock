"""Specialist resolution schemas.

Per design doc § Job templates, agents, skills, and hooks § Specialist
resolution and materialisation API.

Two-tier resolution for v0 (override → global). Filesystem is canonical; no
snapshot. Models here describe what's loaded into memory after resolution,
not the on-disk files themselves.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentDef(BaseModel):
    """A loaded agent definition (frontmatter + body of the .md file)."""

    model_config = ConfigDict(extra="forbid")

    agent_ref: str = Field(min_length=1, description="canonical id — file basename")
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    model: str = Field(min_length=1, description="e.g. claude-opus-4-7")
    tools: list[str] | None = None
    allowed_skills: list[str] | None = None
    hammock_meta: dict[str, Any] | None = None
    body: str = Field(description="prompt body after frontmatter")


class SkillDef(BaseModel):
    """A loaded skill definition (slim summary; full SKILL.md lives on disk)."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    triggering_summary: str = Field(min_length=1)


class AgentEntry(BaseModel):
    """One row in a SpecialistCatalogue's agent listing."""

    model_config = ConfigDict(extra="forbid")

    agent_ref: str = Field(min_length=1)
    source: Literal["override", "global"]
    has_override_for_project: bool
    description: str
    model: str
    allowed_skills: list[str] | None = None
    tunable_fields: list[str] | None = None


class SkillEntry(BaseModel):
    """One row in a SpecialistCatalogue's skill listing."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1)
    source: Literal["override", "global"]
    has_override_for_project: bool
    description: str
    triggering_summary: str


class SpecialistCatalogue(BaseModel):
    """The set of agents + skills resolved for a project at a moment in time."""

    model_config = ConfigDict(extra="forbid")

    project_slug: str = Field(min_length=1)
    agents: list[AgentEntry] = Field(default_factory=list)
    skills: list[SkillEntry] = Field(default_factory=list)


class MaterialisedSpawn(BaseModel):
    """Result of materialise_for_spawn — paths the Job Driver hands to the CLI."""

    model_config = ConfigDict(extra="forbid")

    agents_json: str = Field(description="path to materialised agents.json")
    settings_path: str = Field(description="path to materialised settings dir")
