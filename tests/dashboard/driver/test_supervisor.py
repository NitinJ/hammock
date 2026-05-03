"""Tests for dashboard.driver.supervisor."""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dashboard.driver.supervisor import Supervisor


def _make_supervisor(now: datetime | None = None, interval: float = 30.0) -> Supervisor:
    now_fn = (lambda: now) if now else None
    return Supervisor(
        heartbeat_interval=interval,
        now_fn=now_fn,  # type: ignore[arg-type]
    )


def test_heartbeat_not_stale_when_fresh(tmp_path: Path) -> None:
    """A freshly touched heartbeat file is not stale."""
    hb = tmp_path / "heartbeat"
    hb.write_text("")  # touch

    now = datetime.now(UTC)
    sup = _make_supervisor(now=now)
    assert sup.is_stale(hb) is False


def test_heartbeat_stale_when_old(tmp_path: Path) -> None:
    """Heartbeat last-modified > 3x interval ago → stale."""
    hb = tmp_path / "heartbeat"
    hb.write_text("")

    # Set mtime to 100 seconds ago (> 90s threshold for default 30s interval)
    old_time = time.time() - 100
    os.utime(hb, (old_time, old_time))

    sup = _make_supervisor(interval=30.0)
    assert sup.is_stale(hb) is True


def test_heartbeat_not_stale_when_recent(tmp_path: Path) -> None:
    """Heartbeat 10s old is not stale (< 90s threshold)."""
    hb = tmp_path / "heartbeat"
    hb.write_text("")

    recent_time = time.time() - 10
    os.utime(hb, (recent_time, recent_time))

    sup = _make_supervisor(interval=30.0)
    assert sup.is_stale(hb) is False


def test_heartbeat_stale_when_missing(tmp_path: Path) -> None:
    """Missing heartbeat file is treated as stale."""
    hb = tmp_path / "heartbeat-does-not-exist"
    sup = _make_supervisor()
    assert sup.is_stale(hb) is True


def test_stale_threshold_calculation() -> None:
    """stale_threshold_seconds = heartbeat_interval x stale_factor."""
    sup = Supervisor(heartbeat_interval=30.0, stale_factor=3)
    assert sup.stale_threshold_seconds() == pytest.approx(90.0)


def test_get_pid_reads_pid_file(tmp_path: Path) -> None:
    """get_pid returns the integer in the PID file."""
    pid_path = tmp_path / "job-driver.pid"
    pid_path.write_text("12345\n")

    sup = _make_supervisor()
    assert sup.get_pid(pid_path) == 12345


def test_get_pid_returns_none_when_missing(tmp_path: Path) -> None:
    """Missing PID file returns None."""
    pid_path = tmp_path / "job-driver.pid"
    sup = _make_supervisor()
    assert sup.get_pid(pid_path) is None


def test_get_pid_returns_none_on_corrupt_file(tmp_path: Path) -> None:
    """Corrupt PID file (non-integer content) returns None."""
    pid_path = tmp_path / "job-driver.pid"
    pid_path.write_text("not-a-pid\n")

    sup = _make_supervisor()
    assert sup.get_pid(pid_path) is None


def test_is_stale_uses_injected_clock(tmp_path: Path) -> None:
    """is_stale() uses now_fn instead of wall-clock time.time().

    Codex-review (Minor): Supervisor previously stored now_fn but bypassed it.
    """
    hb = tmp_path / "heartbeat"
    hb.write_text("")
    file_mtime = hb.stat().st_mtime

    # Pin "now" to 100s after the file's mtime — should be stale (>90s).
    fake_now = datetime.fromtimestamp(file_mtime + 100, tz=UTC)
    sup = Supervisor(heartbeat_interval=30.0, stale_factor=3, now_fn=lambda: fake_now)
    assert sup.is_stale(hb) is True

    # Pin "now" to 10s after — should NOT be stale.
    fake_now2 = datetime.fromtimestamp(file_mtime + 10, tz=UTC)
    sup2 = Supervisor(heartbeat_interval=30.0, stale_factor=3, now_fn=lambda: fake_now2)
    assert sup2.is_stale(hb) is False
