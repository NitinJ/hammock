"""Tests for `dashboard.specialist.resolver`.

Per `docs/v0-alignment-report.md` Plan #3: project-level agent
overrides at ``<repo>/.hammock/agent-overrides/<ref>.md`` (skill
overrides at ``<repo>/.hammock/skill-overrides/<id>.md``) need to
be loadable at compile time so per-project customisations actually
take effect at runtime. v0 ships only override discovery; bundled
defaults are out-of-scope here (the design's bundled-agents dir is a
v1+ item).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dashboard.specialist.resolver import (
    AgentParseError,
    parse_agent_md,
    resolve,
)
from shared import paths
from shared.models import ProjectConfig

_AGENT_MD = """\
---
name: bug-report-writer
description: Frames the human prompt as a structured bug report.
model: claude-opus-4-7
tools: [Read, Write]
allowed_skills: [test-driven-development]
---
You are a bug report writer. Frame every prompt as a structured report.
"""


_AGENT_MD_MINIMAL = """\
---
name: minimal-agent
description: Bare minimum
model: claude-opus-4-7
---
Body.
"""


_SKILL_MD = """\
---
skill_id: test-driven-development
description: Write the test first.
triggering_summary: Use when implementing any feature.
---
SKILL.md body here.
"""


def _project(tmp_path: Path, *, slug: str = "p") -> ProjectConfig:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    return ProjectConfig(
        slug=slug,
        name=slug,
        repo_path=str(repo),
        remote_url=f"https://github.com/example/{slug}",
        default_branch="main",
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# parse_agent_md
# ---------------------------------------------------------------------------


def test_parse_agent_md_extracts_frontmatter_and_body() -> None:
    parsed = parse_agent_md("bug-report-writer", _AGENT_MD)
    assert parsed.agent_ref == "bug-report-writer"
    assert parsed.name == "bug-report-writer"
    assert parsed.description.startswith("Frames")
    assert parsed.model == "claude-opus-4-7"
    assert parsed.tools == ["Read", "Write"]
    assert parsed.allowed_skills == ["test-driven-development"]
    assert "structured report" in parsed.body


def test_parse_agent_md_minimal() -> None:
    parsed = parse_agent_md("minimal-agent", _AGENT_MD_MINIMAL)
    assert parsed.tools is None
    assert parsed.allowed_skills is None
    assert parsed.body.strip() == "Body."


def test_parse_agent_md_rejects_missing_frontmatter() -> None:
    with pytest.raises(AgentParseError):
        parse_agent_md("x", "no frontmatter here\nat all")


def test_parse_agent_md_rejects_missing_required_field() -> None:
    bad = "---\nname: foo\n---\nbody"  # no description / model
    with pytest.raises(AgentParseError):
        parse_agent_md("x", bad)


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------


def test_resolve_empty_when_no_overrides(tmp_path: Path) -> None:
    project = _project(tmp_path)
    catalogue = resolve(project)
    assert catalogue.project_slug == project.slug
    assert catalogue.agents == []
    assert catalogue.skills == []


def test_resolve_picks_up_agent_override(tmp_path: Path) -> None:
    project = _project(tmp_path)
    overrides = paths.project_agents_overrides(Path(project.repo_path))
    overrides.mkdir(parents=True)
    (overrides / "bug-report-writer.md").write_text(_AGENT_MD)

    catalogue = resolve(project)
    assert len(catalogue.agents) == 1
    entry = catalogue.agents[0]
    assert entry.agent_ref == "bug-report-writer"
    assert entry.source == "override"
    assert entry.has_override_for_project is True


def test_resolve_picks_up_skill_override(tmp_path: Path) -> None:
    project = _project(tmp_path)
    overrides = paths.project_skills_overrides(Path(project.repo_path))
    overrides.mkdir(parents=True)
    (overrides / "test-driven-development.md").write_text(_SKILL_MD)

    catalogue = resolve(project)
    assert len(catalogue.skills) == 1
    entry = catalogue.skills[0]
    assert entry.skill_id == "test-driven-development"
    assert entry.source == "override"


def test_resolve_skips_malformed_override_with_warning(tmp_path: Path, caplog) -> None:
    """A malformed override .md must not break resolution; just log + skip."""
    import logging

    project = _project(tmp_path)
    overrides = paths.project_agents_overrides(Path(project.repo_path))
    overrides.mkdir(parents=True)
    (overrides / "bad.md").write_text("not yaml frontmatter at all")
    (overrides / "bug-report-writer.md").write_text(_AGENT_MD)

    with caplog.at_level(logging.WARNING):
        catalogue = resolve(project)
    # The valid one is still discovered.
    assert len(catalogue.agents) == 1
    assert catalogue.agents[0].agent_ref == "bug-report-writer"
    # The bad one logged a warning (caplog.text contains formatted output).
    assert "bad.md" in caplog.text


def test_resolve_ignores_non_md_files(tmp_path: Path) -> None:
    project = _project(tmp_path)
    overrides = paths.project_agents_overrides(Path(project.repo_path))
    overrides.mkdir(parents=True)
    (overrides / "README.md").write_text(_AGENT_MD)
    (overrides / "scratch.txt").write_text("ignored")
    catalogue = resolve(project)
    # README.md *is* an .md file → picked up; scratch.txt isn't
    assert len(catalogue.agents) == 1
