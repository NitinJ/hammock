"""Tests for `dashboard.specialist.materialise`.

`materialise_for_spawn(project, stage_run_dir)` writes
``<stage_run_dir>/agents.json`` keyed by ``agent_ref`` whose entries
are the shape claude's ``--agents`` flag accepts (description +
prompt). Returns a :class:`MaterialisedSpawn` pointing at the file
the JobDriver hands to ``RealStageRunner``.

v0 only ships override-tier materialisation; bundled defaults are
out-of-scope.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from dashboard.specialist.materialise import materialise_for_spawn
from dashboard.specialist.resolver import resolve
from shared import paths
from shared.models import ProjectConfig

_AGENT_MD = """\
---
name: bug-report-writer
description: Frames the human prompt as a structured bug report.
model: claude-opus-4-7
tools: [Read, Write]
---
You are a bug report writer. Frame every prompt as a structured report.
"""


def _project(tmp_path: Path) -> ProjectConfig:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    return ProjectConfig(
        slug="p",
        name="p",
        repo_path=str(repo),
        remote_url="https://github.com/example/p",
        default_branch="main",
        created_at=datetime.now(UTC),
    )


def _stage_run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "stage-run"
    d.mkdir()
    return d


def test_materialise_writes_agents_json_with_override(tmp_path: Path) -> None:
    project = _project(tmp_path)
    overrides = paths.project_agents_overrides(Path(project.repo_path))
    overrides.mkdir(parents=True)
    (overrides / "bug-report-writer.md").write_text(_AGENT_MD)

    spawn = materialise_for_spawn(project, _stage_run_dir(tmp_path))

    agents_path = Path(spawn.agents_json)
    assert agents_path.exists()
    payload = json.loads(agents_path.read_text())
    assert "bug-report-writer" in payload
    assert payload["bug-report-writer"]["description"].startswith("Frames")
    assert "bug report writer" in payload["bug-report-writer"]["prompt"]


def test_materialise_writes_empty_agents_json_without_overrides(tmp_path: Path) -> None:
    """No overrides → empty mapping, but the file still lands so callers
    can pass `--agents <path-of-file-contents>` unconditionally."""
    project = _project(tmp_path)
    spawn = materialise_for_spawn(project, _stage_run_dir(tmp_path))
    payload = json.loads(Path(spawn.agents_json).read_text())
    assert payload == {}


def test_materialise_uses_pre_resolved_catalogue(tmp_path: Path) -> None:
    """The materialiser accepts an already-resolved catalogue so the
    Job Driver doesn't have to re-walk the override dirs."""
    project = _project(tmp_path)
    overrides = paths.project_agents_overrides(Path(project.repo_path))
    overrides.mkdir(parents=True)
    (overrides / "bug-report-writer.md").write_text(_AGENT_MD)

    catalogue = resolve(project)
    spawn = materialise_for_spawn(project, _stage_run_dir(tmp_path), catalogue=catalogue)
    payload = json.loads(Path(spawn.agents_json).read_text())
    assert "bug-report-writer" in payload
