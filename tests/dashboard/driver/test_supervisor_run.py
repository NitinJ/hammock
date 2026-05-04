"""Tests for Supervisor.run() — the periodic stale-driver scanner.

Per `docs/v0-alignment-report.md` Plan #7: the dashboard lifespan is
supposed to start a Supervisor background task that detects stale
job-driver heartbeats and respawns the driver. Until this PR the
class only exposed stale/PID *helpers* with no driving loop, so
nothing actually ran in production.

Contract for v0:

- Loop scans non-terminal jobs (SUBMITTED / STAGES_RUNNING /
  BLOCKED_ON_HUMAN) every ``poll_interval`` seconds.
- For each: read pid file + heartbeat. If stale AND the recorded PID
  is **dead**, respawn via ``spawn_driver`` (best-effort: failure
  logs but doesn't break the loop).
- If stale AND PID is **alive**, leave alone (a v1+ refinement may
  SIGTERM + respawn; v0 trusts that the operator will intervene).
- Cancellation propagates cleanly via the asyncio ``CancelledError``.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from dashboard.driver.supervisor import Supervisor
from shared import paths
from shared.atomic import atomic_write_json, atomic_write_text
from shared.models.job import JobConfig, JobState


def _seed_running_job(root: Path, *, slug: str, pid: int, heartbeat_age_s: float) -> Path:
    """Write job.json (STAGES_RUNNING) + pid file + heartbeat with the
    given mtime offset. Returns the job dir."""
    cfg = JobConfig(
        job_id=f"jid-{slug}",
        job_slug=slug,
        project_slug="p",
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="t",
        state=JobState.STAGES_RUNNING,
    )
    atomic_write_json(paths.job_json(slug, root=root), cfg)
    atomic_write_text(paths.job_driver_pid(slug, root=root), f"{pid}\n")
    hb = paths.job_heartbeat(slug, root=root)
    hb.touch()
    if heartbeat_age_s > 0:
        old = datetime.now(UTC).timestamp() - heartbeat_age_s
        os.utime(hb, (old, old))
    return paths.job_dir(slug, root=root)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _dead_pid() -> int:
    """Pick a PID that's almost certainly not running."""
    # PID 999999 is well past the typical /proc/sys/kernel/pid_max default
    return 999999


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_supervisor_run_respawns_driver_when_stale_and_dead(tmp_path: Path) -> None:
    """A stale heartbeat + dead PID triggers `spawn_driver`."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    _seed_running_job(root, slug="j1", pid=_dead_pid(), heartbeat_age_s=120)

    sup = Supervisor()
    spawn = AsyncMock(return_value=12345)
    with patch("dashboard.driver.supervisor.spawn_driver", spawn):
        task = asyncio.create_task(sup.run(root=root, poll_interval=0.05))
        # Give the loop one tick.
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    spawn.assert_awaited()
    args, kwargs = spawn.call_args
    assert args[0] == "j1" or kwargs.get("job_slug") == "j1" or "j1" in args


async def test_supervisor_run_leaves_alive_pid_alone(tmp_path: Path) -> None:
    """Stale heartbeat but PID is alive AND running job_driver → no respawn.

    The supervisor's PID-recycle defence (Codex review on PR #25) checks
    ``/proc/<pid>/cmdline`` for the ``job_driver`` substring, so this
    test patches the helper to simulate a healthy driver process.
    """
    root = tmp_path / "hammock-root"
    root.mkdir()
    _seed_running_job(root, slug="j1", pid=os.getpid(), heartbeat_age_s=120)

    sup = Supervisor()
    spawn = AsyncMock()
    with (
        patch("dashboard.driver.supervisor.spawn_driver", spawn),
        patch("dashboard.driver.supervisor._is_driver_alive", new=lambda _pid: True),
    ):
        task = asyncio.create_task(sup.run(root=root, poll_interval=0.05))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    spawn.assert_not_awaited()


async def test_supervisor_run_leaves_fresh_heartbeats_alone(tmp_path: Path) -> None:
    """Healthy job → no respawn."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    _seed_running_job(root, slug="j1", pid=_dead_pid(), heartbeat_age_s=0)

    sup = Supervisor()
    spawn = AsyncMock()
    with patch("dashboard.driver.supervisor.spawn_driver", spawn):
        task = asyncio.create_task(sup.run(root=root, poll_interval=0.05))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    spawn.assert_not_awaited()


async def test_supervisor_run_skips_terminal_jobs(tmp_path: Path) -> None:
    """COMPLETED / FAILED / ABANDONED jobs aren't scanned at all."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    cfg = JobConfig(
        job_id="jid",
        job_slug="j-done",
        project_slug="p",
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="t",
        state=JobState.COMPLETED,
    )
    atomic_write_json(paths.job_json("j-done", root=root), cfg)

    sup = Supervisor()
    spawn = AsyncMock()
    with patch("dashboard.driver.supervisor.spawn_driver", spawn):
        task = asyncio.create_task(sup.run(root=root, poll_interval=0.05))
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    spawn.assert_not_awaited()


async def test_supervisor_run_cancels_cleanly(tmp_path: Path) -> None:
    """`task.cancel()` returns within one poll cycle."""
    root = tmp_path / "hammock-root"
    root.mkdir()

    sup = Supervisor()
    task = asyncio.create_task(sup.run(root=root, poll_interval=0.05))
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await asyncio.wait_for(task, timeout=1.0)
    except asyncio.CancelledError:
        pass
    assert task.done()
