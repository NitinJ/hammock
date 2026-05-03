"""Tests for dashboard.driver.lifecycle — spawn_driver."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from dashboard.driver.lifecycle import spawn_driver
from shared import paths
from shared.atomic import atomic_write_json
from shared.models.job import JobConfig, JobState


def _make_job(tmp_root: Path, job_slug: str = "test-job") -> Path:
    """Create a minimal job dir with job.json and a one-stage stage-list.yaml."""
    import yaml

    from shared.models.stage import Budget, ExitCondition, InputSpec, OutputSpec, StageDefinition

    job_dir = tmp_root / "jobs" / job_slug
    job_dir.mkdir(parents=True)

    config = JobConfig(
        job_id="jid-001",
        job_slug=job_slug,
        project_slug="test-proj",
        job_type="build-feature",
        created_at=datetime.now(UTC),
        created_by="human",
        state=JobState.SUBMITTED,
    )
    atomic_write_json(job_dir / "job.json", config)

    stage = StageDefinition(
        id="stage-a",
        worker="agent",
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=["out.txt"]),
        budget=Budget(max_turns=5),
        exit_condition=ExitCondition(required_outputs=None),
    )
    stage_list = {"stages": [json.loads(stage.model_dump_json())]}
    (job_dir / "stage-list.yaml").write_text(yaml.dump(stage_list))
    return job_dir


async def test_spawn_driver_returns_pid(tmp_path: Path) -> None:
    """spawn_driver returns a positive integer PID."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    # Write a fixture that makes the stage succeed quickly
    import yaml

    (fixtures_dir / "stage-a.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "artifacts": {"out.txt": "x"}})
    )

    job_dir = _make_job(tmp_path)
    pid = await spawn_driver(
        job_dir.name,
        root=tmp_path,
        fake_fixtures_dir=fixtures_dir,
    )

    assert isinstance(pid, int)
    assert pid > 0


async def test_spawn_driver_writes_pid_file(tmp_path: Path) -> None:
    """spawn_driver writes the PID to jobs/<slug>/job-driver.pid."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    import yaml

    (fixtures_dir / "stage-a.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "artifacts": {"out.txt": "x"}})
    )

    job_dir = _make_job(tmp_path)
    pid = await spawn_driver(
        job_dir.name,
        root=tmp_path,
        fake_fixtures_dir=fixtures_dir,
    )

    pid_path = paths.job_driver_pid(job_dir.name, root=tmp_path)
    assert pid_path.exists()
    assert int(pid_path.read_text().strip()) == pid


async def test_spawn_driver_job_eventually_completes(tmp_path: Path) -> None:
    """Job driven by spawn_driver eventually reaches COMPLETED state."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    import yaml

    (fixtures_dir / "stage-a.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "artifacts": {"out.txt": "done"}})
    )

    job_dir = _make_job(tmp_path)
    await spawn_driver(
        job_dir.name,
        root=tmp_path,
        fake_fixtures_dir=fixtures_dir,
    )

    # Poll until the driver writes the terminal state (max 10 s)
    deadline = time.monotonic() + 10.0
    state = None
    while time.monotonic() < deadline:
        try:
            config = JobConfig.model_validate_json((job_dir / "job.json").read_text())
            state = config.state
            if state in (JobState.COMPLETED, JobState.FAILED, JobState.ABANDONED):
                break
        except Exception:
            pass
        await asyncio.sleep(0.1)

    assert state == JobState.COMPLETED, f"expected COMPLETED, got {state}"


async def test_spawn_driver_grandchild_not_zombied_to_caller(tmp_path: Path) -> None:
    """Double-fork: the spawned PID is NOT a child of this process.

    Codex-review (Important): replaced subprocess.Popen+returncode hack with
    double-fork so the grandchild is re-parented to init/PID 1 and can
    never become a zombie of the caller.
    """
    import os

    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    import yaml

    # Long-running stage so the grandchild is alive when we inspect it
    (fixtures_dir / "stage-a.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "delay_seconds": 5.0, "artifacts": {"out.txt": "x"}})
    )

    job_dir = _make_job(tmp_path, job_slug="grandchild-job")
    pid = await spawn_driver(
        job_dir.name,
        root=tmp_path,
        fake_fixtures_dir=fixtures_dir,
    )

    # /proc/<pid>/status PPid line should NOT be our pid (the parent must be
    # init or some other process — not the test runner).
    status_path = Path(f"/proc/{pid}/status")
    if status_path.exists():
        ppid_line = next(
            (line for line in status_path.read_text().splitlines() if line.startswith("PPid:")),
            None,
        )
        if ppid_line is not None:
            ppid = int(ppid_line.split()[1])
            assert ppid != os.getpid(), (
                f"grandchild PID {pid} is still a child of test process {os.getpid()}; "
                "double-fork detachment failed"
            )

    # Cleanup: terminate the long-running grandchild so the test doesn't leak
    try:
        os.kill(pid, 15)  # SIGTERM
    except ProcessLookupError:
        pass
