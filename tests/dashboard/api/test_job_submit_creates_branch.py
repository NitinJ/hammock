"""Tests that ``POST /api/jobs`` creates the per-job branch on success.

Per `docs/v0-alignment-report.md` Plan #2 + #8 (paired): on a successful
non-dry-run submit, Hammock creates ``hammock/jobs/<slug>`` in the
project's repo (off the project's default branch) before spawning the
driver. This is the parent ref that per-stage branches will inherit
from in PR2.D.

Tested separately from the existing test_job_submit.py because that
suite uses a synthetic ``populated_root`` whose project repo paths
point at ``/tmp/<slug>`` (not real git repos). These tests need real
on-disk git repos.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.code.branches import branch_exists
from dashboard.settings import Settings
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig


def _init_real_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def _seed_project(root: Path, slug: str, repo: Path) -> ProjectConfig:
    project = ProjectConfig(
        slug=slug,
        name=slug,
        repo_path=str(repo),
        remote_url=f"https://github.com/example/{slug}",
        default_branch="main",
        created_at=datetime.now(UTC),
    )
    atomic_write_json(paths.project_json(slug, root=root), project)
    overrides = paths.project_overrides_root(repo)
    (overrides / "job-template-overrides").mkdir(parents=True, exist_ok=True)
    return project


def _make_client(root: Path) -> TestClient:
    return TestClient(create_app(Settings(root=root)))


def _submit(client: TestClient, slug: str) -> str:
    with patch("dashboard.api.jobs.spawn_driver", new_callable=AsyncMock, return_value=12345):
        resp = client.post(
            "/api/jobs",
            json={
                "project_slug": slug,
                "job_type": "fix-bug",
                "title": "fix it",
                "request_text": "do the thing",
            },
        )
    assert resp.status_code == 201, resp.text
    return resp.json()["job_slug"]


def test_submit_creates_job_branch_in_project_repo(tmp_path: Path) -> None:
    root = tmp_path / "hammock-root"
    root.mkdir()
    repo = _init_real_repo(tmp_path / "real-repo")
    _seed_project(root, "real-proj", repo)

    with _make_client(root) as client:
        job_slug = _submit(client, "real-proj")

    assert branch_exists(repo, f"hammock/jobs/{job_slug}"), (
        f"submit must create hammock/jobs/{job_slug} in {repo}"
    )


def test_dry_run_does_not_create_job_branch(tmp_path: Path) -> None:
    """Dry-run is a validation-only path; it must not write any branches."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    repo = _init_real_repo(tmp_path / "real-repo")
    _seed_project(root, "real-proj", repo)

    with _make_client(root) as client:
        resp = client.post(
            "/api/jobs",
            json={
                "project_slug": "real-proj",
                "job_type": "fix-bug",
                "title": "dry",
                "request_text": "do nothing",
                "dry_run": True,
            },
        )
    assert resp.status_code == 201
    job_slug = resp.json()["job_slug"]
    assert not branch_exists(repo, f"hammock/jobs/{job_slug}")


def test_submit_continues_when_repo_is_not_a_git_repo(tmp_path: Path) -> None:
    """Best-effort: if the project's repo_path isn't a real git repo
    (e.g. fake-fixture test setups), submit must still succeed — the
    operator will see the warning + the missing isolation manifests
    when stages run, and v0 fakes don't need real branches."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    fake_repo = tmp_path / "not-a-repo"
    fake_repo.mkdir()  # no `git init`
    _seed_project(root, "fake-proj", fake_repo)

    with _make_client(root) as client:
        # Should not raise; the response still 201s.
        with patch("dashboard.api.jobs.spawn_driver", new_callable=AsyncMock, return_value=42):
            resp = client.post(
                "/api/jobs",
                json={
                    "project_slug": "fake-proj",
                    "job_type": "fix-bug",
                    "title": "x",
                    "request_text": "y",
                },
            )
    assert resp.status_code == 201
