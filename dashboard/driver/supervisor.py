"""Supervisor: heartbeat-stale detection and restart policy.

Per design doc § Process structure § Fault tolerance and recovery:
- Heartbeat written every 30 s by Job Driver.
- Stale at 3x the interval (90 s by default).
- Dashboard's recovery policy: respawn if state is recoverable; mark FAILED
  if it isn't.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

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
