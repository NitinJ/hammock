"""Supervisor: heartbeat-stale detection and restart policy.

Per design doc § Process structure § Fault tolerance and recovery:
- Heartbeat written every 30 s by Job Driver.
- Stale at 3x the interval (90 s by default).
- Dashboard's recovery policy: respawn if state is recoverable; mark FAILED
  if it isn't.

The :meth:`Supervisor.run` coroutine is the long-running entry the
dashboard lifespan creates as a background task (v0 alignment Plan #7).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from dashboard.driver.lifecycle import spawn_driver
from shared import paths
from shared.models.job import JobConfig, JobState

log = logging.getLogger(__name__)

_NON_TERMINAL_JOB_STATES = frozenset(
    {JobState.SUBMITTED, JobState.STAGES_RUNNING, JobState.BLOCKED_ON_HUMAN}
)

# ---------------------------------------------------------------------------
# Stale detection
# ---------------------------------------------------------------------------

HEARTBEAT_INTERVAL: float = 30.0
STALE_FACTOR: int = 3  # heartbeat considered stale after 3x interval


class Supervisor:
    """Heartbeat checks and restart policy for Job Driver processes.

    Parameters
    ----------
    heartbeat_interval:
        Expected seconds between heartbeat touches (default 30).
    stale_factor:
        Multiplier to compute the stale threshold (default 3 → 90 s).
    now_fn:
        Injectable clock for tests.
    """

    def __init__(
        self,
        *,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        stale_factor: int = STALE_FACTOR,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.heartbeat_interval = heartbeat_interval
        self.stale_factor = stale_factor
        self._now: Callable[[], datetime] = now_fn or (lambda: datetime.now(UTC))

    def stale_threshold_seconds(self) -> float:
        return self.heartbeat_interval * self.stale_factor

    def is_stale(self, heartbeat_path: Path) -> bool:
        """Return True if the heartbeat file is absent or older than the stale threshold.

        Uses the injected ``now_fn`` so tests can drive this deterministically.
        """
        if not heartbeat_path.exists():
            return True
        try:
            mtime = heartbeat_path.stat().st_mtime
            age_seconds = self._now().timestamp() - mtime
            return age_seconds > self.stale_threshold_seconds()
        except OSError:
            return True

    def get_pid(self, pid_path: Path) -> int | None:
        """Read and return the PID from *pid_path*, or None on error."""
        try:
            raw = pid_path.read_text().strip()
            return int(raw)
        except (FileNotFoundError, ValueError, OSError):
            return None

    async def run(
        self,
        *,
        root: Path | None = None,
        poll_interval: float = 30.0,
    ) -> None:
        """Long-running scan + respawn loop.

        Iterates ``<root>/jobs/*`` every ``poll_interval`` seconds. For
        each non-terminal job, checks heartbeat staleness; if stale and
        the recorded PID is dead, respawns the driver via
        :func:`spawn_driver` (best-effort: failure logs, loop continues).

        Stale heartbeat with an alive PID is left alone for v0 — a v1+
        refinement may SIGTERM + respawn unhealthy long-running drivers.
        Cancellation propagates via :class:`asyncio.CancelledError`.
        """
        log.info("supervisor started — poll_interval=%.1fs", poll_interval)
        try:
            while True:
                try:
                    await self._scan_once(root)
                except Exception as exc:
                    log.warning("supervisor scan failed: %s", exc)
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            log.info("supervisor cancelled")
            raise

    async def _scan_once(self, root: Path | None) -> None:
        jobs_dir = paths.jobs_dir(root=root)
        if not jobs_dir.is_dir():
            return
        for entry in sorted(jobs_dir.iterdir()):
            if not entry.is_dir():
                continue
            slug = entry.name
            cfg_path = paths.job_json(slug, root=root)
            try:
                cfg = JobConfig.model_validate_json(cfg_path.read_text())
            except (FileNotFoundError, ValueError, OSError):
                continue
            if cfg.state not in _NON_TERMINAL_JOB_STATES:
                continue
            await self._maybe_respawn(slug, root)

    async def _maybe_respawn(self, slug: str, root: Path | None) -> None:
        hb = paths.job_heartbeat(slug, root=root)
        if not self.is_stale(hb):
            return
        pid = self.get_pid(paths.job_driver_pid(slug, root=root))
        if pid is not None and _is_pid_alive(pid):
            log.warning(
                "job %s heartbeat stale but pid %d alive — leaving alone (v0)",
                slug,
                pid,
            )
            return
        log.warning("respawning stale driver for job %s (pid=%s, dead)", slug, pid)
        try:
            await spawn_driver(slug, root=root)
        except Exception as exc:
            log.warning("failed to respawn driver for %s: %s", slug, exc)


def _is_pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
