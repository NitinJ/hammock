"""Specialist resolver — load per-project agent + skill overrides.

Per `docs/v0-alignment-report.md` Plan #3: the design promised a
runtime API for resolving the per-project specialist catalogue, but
v0 only shipped the model. This module closes that gap. v0 scope:

- Walk ``<repo>/.hammock/agent-overrides/*.md`` and parse each as
  ``AgentDef`` (frontmatter + body).
- Walk ``<repo>/.hammock/skill-overrides/*.md`` and parse each as
  ``SkillDef``.
- Return a typed :class:`SpecialistCatalogue` listing what was found,
  marked ``source="override"``.
- Bundled defaults (the "global" tier) are deferred to v1+; the
  catalogue model already carries an ``override | global`` enum so
  the upgrade is additive.

Every parsed file is best-effort: a malformed frontmatter logs a
warning and is skipped rather than aborting the whole resolution.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from shared import paths
from shared.models import ProjectConfig
from shared.models.specialist import (
    AgentDef,
    AgentEntry,
    SkillDef,
    SkillEntry,
    SpecialistCatalogue,
)

log = logging.getLogger(__name__)


class AgentParseError(Exception):
    """Raised when an agent .md file can't be parsed as frontmatter+body."""


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split ``---\\n...---\\n<body>`` into (front_matter_dict, body).

    Raises :class:`AgentParseError` if the file doesn't start with a
    ``---`` line followed by a closing ``---``.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise AgentParseError("file must start with '---' frontmatter delimiter")
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise AgentParseError("missing closing '---' for frontmatter")
    raw_front = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])
    try:
        data = yaml.safe_load(raw_front) or {}
    except yaml.YAMLError as exc:
        raise AgentParseError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentParseError("frontmatter must be a YAML mapping")
    return data, body


def parse_agent_md(agent_ref: str, text: str) -> AgentDef:
    """Parse one agent .md file's text into an :class:`AgentDef`."""
    front, body = _split_frontmatter(text)
    front.setdefault("agent_ref", agent_ref)
    front["body"] = body
    try:
        return AgentDef.model_validate(front)
    except ValidationError as exc:
        raise AgentParseError(f"agent frontmatter validation failed: {exc}") from exc


def _parse_skill_md(skill_id: str, text: str) -> SkillDef:
    front, _body = _split_frontmatter(text)
    front.setdefault("skill_id", skill_id)
    return SkillDef.model_validate(front)


def _agent_entry_from_def(definition: AgentDef) -> AgentEntry:
    return AgentEntry(
        agent_ref=definition.agent_ref,
        source="override",
        has_override_for_project=True,
        description=definition.description,
        model=definition.model,
        allowed_skills=definition.allowed_skills,
        tunable_fields=None,
    )


def _skill_entry_from_def(definition: SkillDef) -> SkillEntry:
    return SkillEntry(
        skill_id=definition.skill_id,
        source="override",
        has_override_for_project=True,
        description=definition.description,
        triggering_summary=definition.triggering_summary,
    )


def resolve(project: ProjectConfig) -> SpecialistCatalogue:
    """Resolve a project's specialist catalogue.

    v0 only walks override directories. Bundled-defaults (tier
    ``"global"``) is a v1+ extension; until then, projects without
    overrides have an empty catalogue.
    """
    repo = Path(project.repo_path)
    agents_dir = paths.project_agents_overrides(repo)
    skills_dir = paths.project_skills_overrides(repo)

    agents: list[AgentEntry] = []
    if agents_dir.is_dir():
        for f in sorted(agents_dir.glob("*.md")):
            try:
                definition = parse_agent_md(f.stem, f.read_text())
            except AgentParseError as exc:
                log.warning("skipping malformed agent override %s: %s", f, exc)
                continue
            except OSError as exc:
                log.warning("skipping unreadable agent override %s: %s", f, exc)
                continue
            agents.append(_agent_entry_from_def(definition))

    skills: list[SkillEntry] = []
    if skills_dir.is_dir():
        for f in sorted(skills_dir.glob("*.md")):
            try:
                definition = _parse_skill_md(f.stem, f.read_text())
            except (AgentParseError, ValidationError) as exc:
                log.warning("skipping malformed skill override %s: %s", f, exc)
                continue
            except OSError as exc:
                log.warning("skipping unreadable skill override %s: %s", f, exc)
                continue
            skills.append(_skill_entry_from_def(definition))

    return SpecialistCatalogue(project_slug=project.slug, agents=agents, skills=skills)
