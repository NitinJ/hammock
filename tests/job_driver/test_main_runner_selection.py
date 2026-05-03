"""Tests for `python -m job_driver` runner selection.

Covers the v1+ wiring that lets the entry point pick FakeStageRunner vs
RealStageRunner based on whether ``--fake-fixtures`` is passed. Tests
exercise ``_build_runner`` (the pure runner-selection helper) directly
so they don't go through ``asyncio.run`` — that would leave event-loop
debris attributed to the next test by pytest's unraisable-exception
capture, surfacing as a flaky failure on the hypothesis property tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from job_driver import __main__ as entry
from job_driver.stage_runner import FakeStageRunner
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig
from shared.models.job import JobConfig, JobState


def _seed_job(root: Path, *, job_slug: str = "test-job", project_slug: str = "test-proj") -> Path:
    """Write a minimal project.json + job.json so _resolve_project_root works."""
    repo_path = root / "fake-repo"
    repo_path.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig(
        slug=project_slug,
        name=project_slug,
        repo_path=str(repo_path),
        remote_url=f"https://github.com/example/{project_slug}",
        default_branch="main",
        created_at=datetime.now(UTC),
    )
    atomic_write_json(paths.project_json(project_slug, root=root), project)
    job = JobConfig(
        job_id="jid-001",
        job_slug=job_slug,
        project_slug=project_slug,
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="test",
        state=JobState.SUBMITTED,
    )
    atomic_write_json(paths.job_json(job_slug, root=root), job)
    return repo_path


def test_resolve_project_root_returns_repo_path(tmp_path: Path) -> None:
    repo = _seed_job(tmp_path, job_slug="job-a", project_slug="proj-a")
    assert entry._resolve_project_root("job-a", tmp_path) == repo


def test_resolve_project_root_missing_job_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        entry._resolve_project_root("no-such-job", tmp_path)


def test_build_runner_uses_fake_when_fixtures_set(tmp_path: Path) -> None:
    """--fake-fixtures path → FakeStageRunner; project lookup is skipped."""
    fixtures = tmp_path / "fakes"
    fixtures.mkdir()
    runner = entry._build_runner(
        job_slug="any-slug",
        root=tmp_path,
        fake_fixtures=str(fixtures),
        claude_binary="claude",
    )
    assert isinstance(runner, FakeStageRunner)


def test_build_runner_uses_real_when_fake_fixtures_absent(tmp_path: Path) -> None:
    """No --fake-fixtures → RealStageRunner constructed with project's repo."""
    repo = _seed_job(tmp_path, job_slug="job-real", project_slug="proj-real")
    real_mock = MagicMock(name="RealStageRunnerMock")
    with (
        patch.object(entry, "RealStageRunner", real_mock),
        patch.object(entry.shutil, "which", return_value="/fake/path/claude"),
    ):
        runner = entry._build_runner(
            job_slug="job-real",
            root=tmp_path,
            fake_fixtures=None,
            claude_binary="claude",
        )
    real_mock.assert_called_once_with(project_root=repo, claude_binary="claude")
    assert runner is real_mock.return_value


def test_build_runner_real_respects_claude_binary_override(tmp_path: Path) -> None:
    repo = _seed_job(tmp_path, job_slug="job-x", project_slug="proj-x")
    real_mock = MagicMock(name="RealStageRunnerMock")
    with (
        patch.object(entry, "RealStageRunner", real_mock),
        patch.object(entry.shutil, "which", return_value="/opt/claude/bin/claude"),
    ):
        entry._build_runner(
            job_slug="job-x",
            root=tmp_path,
            fake_fixtures=None,
            claude_binary="/opt/claude/bin/claude",
        )
    real_mock.assert_called_once_with(project_root=repo, claude_binary="/opt/claude/bin/claude")


def test_build_runner_real_propagates_missing_job(tmp_path: Path) -> None:
    """When real-runner is selected and job.json is missing, the helper
    raises (main() catches and exits 2 with a clean stderr message)."""
    with pytest.raises(FileNotFoundError):
        entry._build_runner(
            job_slug="no-such-job",
            root=tmp_path,
            fake_fixtures=None,
            claude_binary="claude",
        )


def test_build_runner_real_preflights_missing_claude_binary(tmp_path: Path) -> None:
    """When `claude` isn't on PATH and the override isn't a real file, fail
    in the parent process with a clear message — not later in the
    detached grandchild."""
    _seed_job(tmp_path, job_slug="job-pf", project_slug="proj-pf")
    with patch.object(entry.shutil, "which", return_value=None):
        with pytest.raises(FileNotFoundError, match=r"claude.*binary not found"):
            entry._build_runner(
                job_slug="job-pf",
                root=tmp_path,
                fake_fixtures=None,
                claude_binary="claude",
            )


def test_build_runner_real_accepts_absolute_claude_path(tmp_path: Path) -> None:
    """Absolute-path override skips the PATH lookup and only checks the
    file exists. (Operators set HAMMOCK_CLAUDE_BINARY to install paths
    that aren't on the daemon's $PATH.)"""
    _seed_job(tmp_path, job_slug="job-abs", project_slug="proj-abs")
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\nexit 0\n")
    fake_claude.chmod(0o755)
    real_mock = MagicMock(name="RealStageRunnerMock")
    with (
        patch.object(entry, "RealStageRunner", real_mock),
        patch.object(entry.shutil, "which", return_value=None),
    ):
        entry._build_runner(
            job_slug="job-abs",
            root=tmp_path,
            fake_fixtures=None,
            claude_binary=str(fake_claude),
        )
    real_mock.assert_called_once()


def test_main_exits_2_on_unresolvable_project(tmp_path: Path, capsys) -> None:
    """End-to-end coverage of main()'s try/except + exit-2 stderr path —
    not just _build_runner. (Codex review L1.)"""
    argv = ["job_driver", "no-such-job", "--root", str(tmp_path)]
    with patch("sys.argv", argv), pytest.raises(SystemExit) as excinfo:
        entry.main()
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "no-such-job" in err
    assert "cannot resolve project root" in err
