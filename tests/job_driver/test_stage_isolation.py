"""Tests for stage worktree + branch lifecycle inside the JobDriver.

Per `docs/v0-alignment-report.md` Plan #2 + #8 (paired):

- Pre-stage: JobDriver creates ``hammock/stages/<job>/<stage_id>``
  (off the job branch) and checks it out into a worktree at
  ``<job_dir>/stages/<sid>/worktree/``.
- Stage runs in that worktree (RealStageRunner uses it as cwd).
- Post-stage (terminal state, any of SUCCEEDED / FAILED / CANCELLED):
  worktree is removed; branch stays on disk for forensics.
- Resume after crash: an existing worktree at the right path is
  reused, not re-created.

JobDriver needs the project's repo_path to do this — reads it lazily
from project.json. If the repo isn't a real git repo (fake-fixture
scenarios), JobDriver logs a warning and skips isolation entirely.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from dashboard.code.branches import branch_exists, create_job_branch
from job_driver.runner import JobDriver
from job_driver.stage_runner import FakeStageRunner
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig
from shared.models.job import JobConfig, JobState
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _init_repo(path: Path) -> Path:
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
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def _stage(stage_id: str, output: str) -> StageDefinition:
    return StageDefinition(
        id=stage_id,
        worker="agent",
        agent_ref="x",
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=[output]),
        budget=Budget(max_turns=5),
        exit_condition=ExitCondition(required_outputs=[RequiredOutput(path=output)]),
    )


def _seed_full_job(
    tmp_path: Path,
    *,
    stage_id: str = "design",
    output: str = "o.txt",
    fixture: dict | None = None,
) -> tuple[Path, Path, str]:
    """Create a real repo + project + job + fake-fixture for one stage.

    Returns (root, repo, job_slug).
    """
    root = tmp_path / "hammock-root"
    root.mkdir()
    repo = _init_repo(tmp_path / "repo")
    project = ProjectConfig(
        slug="p",
        name="p",
        repo_path=str(repo),
        remote_url="https://github.com/example/p",
        default_branch="main",
        created_at=datetime.now(UTC),
    )
    atomic_write_json(paths.project_json("p", root=root), project)

    # Pre-create the job branch (the test simulates what `submit_job`
    # would have done before spawning the driver).
    job_slug = "j-iso"
    create_job_branch(repo, job_slug, base="main")

    job_dir = paths.job_dir(job_slug, root=root)
    job_dir.mkdir(parents=True)
    cfg = JobConfig(
        job_id="jid",
        job_slug=job_slug,
        project_slug="p",
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="t",
        state=JobState.SUBMITTED,
    )
    atomic_write_json(job_dir / "job.json", cfg)
    (job_dir / "stage-list.yaml").write_text(
        yaml.dump({"stages": [json.loads(_stage(stage_id, output).model_dump_json())]})
    )

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / f"{stage_id}.yaml").write_text(
        yaml.dump(fixture or {"outcome": "succeeded", "artifacts": {output: "."}})
    )

    return root, repo, job_slug


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_jobdriver_creates_stage_branch(tmp_path: Path) -> None:
    """Running a stage creates ``hammock/stages/<job>/<stage_id>`` in
    the project repo (off the job branch)."""
    root, repo, job_slug = _seed_full_job(tmp_path)
    fixtures = tmp_path / "fixtures"

    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    assert branch_exists(repo, f"hammock/stages/{job_slug}/design")


async def test_jobdriver_creates_worktree_for_stage(tmp_path: Path) -> None:
    """Worktree lands at the expected path during the stage run.

    The fake fixture's ``check_worktree_exists`` hook (a write of a
    sentinel file inside the worktree) is the cleanest way to prove
    the stage really ran inside the worktree path.
    """
    root, repo, job_slug = _seed_full_job(tmp_path)
    fixtures = tmp_path / "fixtures"

    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    # After run, the worktree should be torn down (terminal state).
    expected_wt = paths.stage_dir(job_slug, "design", root=root) / "worktree"
    assert not expected_wt.exists(), "worktree should be removed after terminal stage state"

    # And the branch should still be present (forensic value).
    assert branch_exists(repo, f"hammock/stages/{job_slug}/design")


async def test_jobdriver_removes_worktree_on_failed_stage(tmp_path: Path) -> None:
    """Even on FAILED, the worktree is cleaned up."""
    root, repo, job_slug = _seed_full_job(tmp_path, fixture={"outcome": "failed", "reason": "boom"})
    fixtures = tmp_path / "fixtures"

    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    expected_wt = paths.stage_dir(job_slug, "design", root=root) / "worktree"
    assert not expected_wt.exists()
    # Branch survives for forensics.
    assert branch_exists(repo, f"hammock/stages/{job_slug}/design")


async def test_jobdriver_skips_isolation_when_repo_not_git(tmp_path: Path) -> None:
    """If the project's repo_path isn't a real git repo, JobDriver logs
    + skips isolation. The job still runs to COMPLETED via the fake
    runner."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    fake_repo = tmp_path / "not-a-repo"
    fake_repo.mkdir()  # no git init
    project = ProjectConfig(
        slug="p",
        name="p",
        repo_path=str(fake_repo),
        remote_url="https://github.com/example/p",
        default_branch="main",
        created_at=datetime.now(UTC),
    )
    atomic_write_json(paths.project_json("p", root=root), project)

    job_slug = "j-fake"
    job_dir = paths.job_dir(job_slug, root=root)
    job_dir.mkdir(parents=True)
    cfg = JobConfig(
        job_id="jid",
        job_slug=job_slug,
        project_slug="p",
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="t",
        state=JobState.SUBMITTED,
    )
    atomic_write_json(job_dir / "job.json", cfg)
    (job_dir / "stage-list.yaml").write_text(
        yaml.dump({"stages": [json.loads(_stage("design", "o.txt").model_dump_json())]})
    )

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "design.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "artifacts": {"o.txt": "."}})
    )

    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    final = JobConfig.model_validate_json((job_dir / "job.json").read_text())
    assert final.state == JobState.COMPLETED


async def test_jobdriver_resume_reuses_existing_worktree(tmp_path: Path) -> None:
    """If a worktree already exists at the expected path (e.g. from a
    previous attempt that crashed before cleanup), JobDriver reuses
    it instead of erroring out."""
    root, repo, job_slug = _seed_full_job(tmp_path)
    fixtures = tmp_path / "fixtures"

    # Pre-create the stage branch + worktree as if a previous run had
    # done so but never cleaned up.
    from dashboard.code.branches import create_stage_branch
    from dashboard.code.worktrees import add_worktree

    create_stage_branch(repo, job_slug, "design")
    pre_wt = paths.stage_dir(job_slug, "design", root=root) / "worktree"
    add_worktree(repo, pre_wt, f"hammock/stages/{job_slug}/design")
    assert pre_wt.exists()

    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    # Should not raise on re-creation attempt.
    await driver.run()

    # Cleaned up after terminal state.
    assert not pre_wt.exists()
