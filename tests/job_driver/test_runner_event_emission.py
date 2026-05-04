"""Tests for the new event emissions added in P4 of the real-claude
e2e precondition track:

- ``worktree_created`` after a successful ``add_worktree`` call from
  the JobDriver's stage-isolation setup.
- ``worker_exit`` after every stage runner returns (succeeded or not),
  carrying the runner's reported ``exit_code``.

The ``worktree_destroyed`` event type is part of the taxonomy but
not yet wired (the v0 driver doesn't tear down stage worktrees;
post-v0 cleanup will wire emission then). This is asserted in
``tests/shared/test_models_events.py``.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import yaml

from job_driver.runner import JobDriver
from job_driver.stage_runner import FakeStageRunner, StageResult
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig
from shared.models.events import Event
from shared.models.job import JobConfig, JobState
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
)


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


def _seed_one_stage_job(
    tmp_path: Path,
    *,
    stage_id: str = "stage-a",
    output: str = "out.txt",
    fixture_outcome: str = "succeeded",
) -> tuple[Path, Path, str]:
    """Create root + repo + project + job + fake-fixture for one stage.
    Returns (root, repo, job_slug)."""
    from dashboard.code.branches import create_job_branch

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

    job_slug = "j-events"
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
    stage = StageDefinition(
        id=stage_id,
        worker="agent",
        agent_ref="x",
        inputs=InputSpec(),
        outputs=OutputSpec(required=[output]),
        budget=Budget(max_turns=5),
        exit_condition=ExitCondition(required_outputs=[RequiredOutput(path=output)]),
    )
    (job_dir / "stage-list.yaml").write_text(
        yaml.dump({"stages": [json.loads(stage.model_dump_json())]})
    )

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    fixture: dict[str, object] = {"outcome": fixture_outcome}
    if fixture_outcome == "succeeded":
        fixture["artifacts"] = {output: "."}
    (fixtures / f"{stage_id}.yaml").write_text(yaml.dump(fixture))

    return root, repo, job_slug


def _read_events(root: Path, job_slug: str) -> list[Event]:
    p = paths.job_events_jsonl(job_slug, root=root)
    if not p.exists():
        return []
    return [Event.model_validate_json(line) for line in p.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------


async def test_jobdriver_emits_worktree_created(tmp_path: Path) -> None:
    """After the JobDriver calls add_worktree, a ``worktree_created``
    event reaches events.jsonl with the worktree path + branch in the
    payload — the e2e test asserts on this for outcome #13."""
    root, _repo, job_slug = _seed_one_stage_job(tmp_path)
    fixtures = tmp_path / "fixtures"

    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    events = _read_events(root, job_slug)
    created_events = [e for e in events if e.event_type == "worktree_created"]
    assert len(created_events) == 1, [e.event_type for e in events]
    payload = created_events[0].payload
    assert "path" in payload
    assert "branch" in payload
    assert payload["stage_id"] == "stage-a"


async def test_jobdriver_emits_worker_exit_with_exit_code(tmp_path: Path) -> None:
    """Every stage runner return → ``worker_exit`` event with the
    runner's reported exit_code (None for fake-fixture flows is
    acceptable; real-mode populates it)."""
    root, _repo, job_slug = _seed_one_stage_job(tmp_path)
    fixtures = tmp_path / "fixtures"

    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    events = _read_events(root, job_slug)
    exits = [e for e in events if e.event_type == "worker_exit"]
    assert len(exits) == 1
    payload = exits[0].payload
    assert payload["stage_id"] == "stage-a"
    assert "exit_code" in payload  # may be None for FakeStageRunner
    assert payload["succeeded"] is True


async def test_jobdriver_emits_worker_exit_on_stage_failure(tmp_path: Path) -> None:
    """The event must fire on failure too — that's how the e2e test
    distinguishes "subprocess crashed" from "Hammock cancelled it"."""
    root, _repo, job_slug = _seed_one_stage_job(tmp_path, fixture_outcome="failed")
    fixtures = tmp_path / "fixtures"

    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    events = _read_events(root, job_slug)
    exits = [e for e in events if e.event_type == "worker_exit"]
    assert len(exits) == 1
    assert exits[0].payload["succeeded"] is False


async def test_jobdriver_emits_worker_exit_on_runner_exception(tmp_path: Path) -> None:
    """Codex review on PR #28: when StageRunner.run() raises, the
    worker_exit event must still fire so the e2e contract holds."""

    class _CrashingRunner:
        async def run(
            self,
            stage_def: StageDefinition,
            job_dir: Path,
            stage_run_dir: Path,
        ) -> StageResult:
            del stage_def, job_dir, stage_run_dir
            raise RuntimeError("simulated runner crash")

    root, _repo, job_slug = _seed_one_stage_job(tmp_path)
    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=_CrashingRunner(),
        heartbeat_interval=0.1,
    )
    await driver.run()

    events = _read_events(root, job_slug)
    exits = [e for e in events if e.event_type == "worker_exit"]
    assert len(exits) == 1
    payload = exits[0].payload
    assert payload["succeeded"] is False
    assert "simulated runner crash" in (payload.get("reason") or "")


async def test_worktree_created_payload_path_is_absolute(tmp_path: Path) -> None:
    """Codex review on PR #28: lock the path-format contract so the
    e2e test doesn't churn on absolute-vs-relative ambiguity."""
    root, _repo, job_slug = _seed_one_stage_job(tmp_path)
    fixtures = tmp_path / "fixtures"
    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    events = _read_events(root, job_slug)
    created = next(e for e in events if e.event_type == "worktree_created")
    assert Path(created.payload["path"]).is_absolute()


async def test_real_stage_runner_populates_exit_code() -> None:
    """RealStageRunner must set ``StageResult.exit_code`` from
    proc.returncode so worker_exit carries the real number, not None."""
    # StageResult must accept the new field; the actual subprocess
    # capture is exercised in test_real_stage_runner.py.
    result = StageResult(succeeded=True, exit_code=0)
    assert result.exit_code == 0
    result_failed = StageResult(succeeded=False, exit_code=2)
    assert result_failed.exit_code == 2
