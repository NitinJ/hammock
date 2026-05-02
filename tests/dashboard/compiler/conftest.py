"""Shared fixtures for compiler tests."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from shared import paths
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig

# The bundled templates directory; tests use this directly so they exercise
# the same YAML the production compiler would load.
BUNDLED_TEMPLATES_DIR: Path = (
    Path(__file__).parent.parent.parent.parent / "hammock" / "templates" / "job-templates"
)


@pytest.fixture
def hammock_root(tmp_path: Path) -> Iterator[Path]:
    root = tmp_path / "hammock-root"
    root.mkdir()
    yield root


@pytest.fixture
def fake_project(tmp_path: Path, hammock_root: Path) -> ProjectConfig:
    """A registered project with a fake repo path. Writes ``project.json``."""
    repo_path = tmp_path / "fake-repo"
    repo_path.mkdir()
    (repo_path / ".git").mkdir()
    project = ProjectConfig(
        slug="fake-project",
        name="fake-project",
        repo_path=str(repo_path),
        remote_url="https://github.com/example/fake.git",
        default_branch="main",
        created_at=datetime(2026, 5, 2, tzinfo=UTC),
    )
    atomic_write_json(paths.project_json("fake-project", root=hammock_root), project)
    # Override skeleton (empty)
    overrides = paths.project_overrides_root(repo_path)
    (overrides / "job-template-overrides").mkdir(parents=True, exist_ok=True)
    return project
