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
import time
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

# Respawn policy (Codex review on PR #25):
# - ``RESPAWN_GRACE_SECONDS``: ignore freshly-submitted jobs whose driver
#   simply hasn't written its pid yet — avoids double-spawn during the
#   spawn_driver → pid-write window.
# - ``RESPAWN_BACKOFF_BASE_S`` / ``MAX_RESPAWN_ATTEMPTS``: bound the
#   respawn rate of a driver that crashes immediately on startup.
RESPAWN_GRACE_SECONDS: float = 60.0
RESPAWN_BACKOFF_BASE_S: float = 60.0
RESPAWN_BACKOFF_MAX_S: float = 600.0
MAX_RESPAWN_ATTEMPTS: int = 5


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
        # Per-slug respawn ledger: (attempts, last_attempt_monotonic_seconds).
        self._respawn_attempts: dict[str, tuple[int, float]] = {}

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
            await self._maybe_respawn(slug, cfg, root)

    async def _maybe_respawn(self, slug: str, cfg: JobConfig, root: Path | None) -> None:
        hb = paths.job_heartbeat(slug, root=root)
        if not self.is_stale(hb):
            return

        # Startup grace: a freshly-SUBMITTED job whose driver hasn't
        # yet written the pid file would otherwise be respawned by the
        # supervisor's first scan, racing with the original driver.
        # Skip jobs younger than ``RESPAWN_GRACE_SECONDS`` whose pid
        # file is absent.
        pid_path = paths.job_driver_pid(slug, root=root)
        pid = self.get_pid(pid_path)
        if pid is None:
            age = (self._now() - cfg.created_at).total_seconds()
            if age < RESPAWN_GRACE_SECONDS:
                return

        if pid is not None and _is_driver_alive(pid):
            log.warning(
                "job %s heartbeat stale but pid %d alive — leaving alone (v0)",
                slug,
                pid,
            )
            return

        # Respawn backoff: an immediately-crashing driver would loop
        # respawn-die-respawn at the poll cadence. Track attempts and
        # back off exponentially, capped at ``MAX_RESPAWN_ATTEMPTS``.
        attempts, last_at = self._respawn_attempts.get(slug, (0, 0.0))
        now_mono = time.monotonic()
        backoff = min(RESPAWN_BACKOFF_BASE_S * (2**attempts), RESPAWN_BACKOFF_MAX_S)
        if attempts > 0 and (now_mono - last_at) < backoff:
            return
        if attempts >= MAX_RESPAWN_ATTEMPTS:
            log.error(
                "job %s exceeded %d respawn attempts; leaving alone",
                slug,
                MAX_RESPAWN_ATTEMPTS,
            )
            return

        log.warning(
            "respawning stale driver for job %s (pid=%s, attempt=%d)",
            slug,
            pid,
            attempts + 1,
        )
        self._respawn_attempts[slug] = (attempts + 1, now_mono)
        try:
            await spawn_driver(slug, root=root)
        except Exception as exc:
            log.warning("failed to respawn driver for %s: %s", slug, exc)


def _is_driver_alive(pid: int) -> bool:
    """Return True if *pid* is alive AND running ``job_driver``.

    ``os.kill(pid, 0)`` alone is unreliable across PID-recycling: a
    recycled PID owned by an unrelated process would mask a dead
    driver. On Linux we additionally confirm the process's cmdline
    includes ``job_driver``. On non-Linux platforms (or if /proc is
    unreadable) we fall back to liveness only.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Different user; we can't signal, but the process exists.
        # Drop to /proc check for cmdline confirmation.
        pass

    cmdline_path = Path(f"/proc/{pid}/cmdline")
    try:
        cmdline = cmdline_path.read_bytes()
    except OSError:
        # /proc unavailable (non-Linux, container w/o /proc, ...).
        # Best-effort: trust the kill(0) signal we already passed.
        return True
    return b"job_driver" in cmdline


# Back-compat alias for any external callers; same semantics now.
_is_pid_alive = _is_driver_alive
