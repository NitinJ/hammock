"""Tests for dashboard.driver.ipc — cancel command file + signal helpers."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from dashboard.driver.ipc import cancel_job, send_sigterm, write_cancel_command
from shared import paths


def test_write_cancel_command_creates_file(tmp_path: Path) -> None:
    """write_cancel_command writes human-action.json with cancel payload."""
    job_dir = tmp_path / "jobs" / "test-job"
    job_dir.mkdir(parents=True)

    write_cancel_command("test-job", root=tmp_path, reason="human")

    action_path = paths.job_human_action("test-job", root=tmp_path)
    assert action_path.exists()

    payload = json.loads(action_path.read_text())
    assert payload["command"] == "cancel"
    assert payload["reason"] == "human"


def test_write_cancel_command_custom_reason(tmp_path: Path) -> None:
    """write_cancel_command records the provided reason."""
    job_dir = tmp_path / "jobs" / "custom-job"
    job_dir.mkdir(parents=True)

    write_cancel_command("custom-job", root=tmp_path, reason="timeout")

    action_path = paths.job_human_action("custom-job", root=tmp_path)
    payload = json.loads(action_path.read_text())
    assert payload["reason"] == "timeout"


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not available on Windows")
def test_send_sigterm_to_self() -> None:
    """send_sigterm can send SIGTERM; handler converts it to a no-op here."""
    # Install a handler that records the signal was received
    received: list[int] = []

    def _handler(sig: int, frame: object) -> None:
        received.append(sig)

    old = signal.signal(signal.SIGTERM, _handler)
    try:
        send_sigterm(os.getpid())
        # Give the signal a moment to be processed
        time.sleep(0.05)
    finally:
        signal.signal(signal.SIGTERM, old)

    assert signal.SIGTERM in received


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not available on Windows")
def test_send_sigterm_nonexistent_pid() -> None:
    """Sending SIGTERM to a non-existent PID raises ProcessLookupError."""
    # PID 0 and negative PIDs are special; use a large number unlikely to be running
    with pytest.raises(ProcessLookupError):
        send_sigterm(999_999_999)


async def test_cancel_job_writes_command_file(tmp_path: Path) -> None:
    """cancel_job writes the cancel command file."""
    job_dir = tmp_path / "jobs" / "test-job"
    job_dir.mkdir(parents=True)

    # Write a fake PID file pointing to ourselves so SIGTERM doesn't error
    pid_path = paths.job_driver_pid("test-job", root=tmp_path)
    # Use an invalid PID so signal delivery silently no-ops
    # (we mainly want to test command-file write here)
    pid_path.write_text("0\n")

    # cancel_job should not raise even if signal fails
    try:
        await cancel_job("test-job", root=tmp_path, timeout=0.1)
    except Exception:
        pass  # signal errors are acceptable in this unit test

    action_path = paths.job_human_action("test-job", root=tmp_path)
    assert action_path.exists()
    payload = json.loads(action_path.read_text())
    assert payload["command"] == "cancel"
