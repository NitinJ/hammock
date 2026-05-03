"""Tests for dashboard.driver.ipc — cancel command file + signal helpers."""

from __future__ import annotations

import json
import os
import signal
import sys
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
def test_send_sigterm_calls_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """send_sigterm calls os.kill with the given PID and SIGTERM."""
    calls: list[tuple[int, int]] = []

    def _fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr(os, "kill", _fake_kill)
    send_sigterm(12345)

    assert calls == [(12345, signal.SIGTERM)]


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not available on Windows")
def test_send_sigterm_nonexistent_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sending SIGTERM to a non-existent PID raises ProcessLookupError."""

    def _fake_kill(pid: int, sig: int) -> None:
        raise ProcessLookupError(f"No process with pid {pid}")

    monkeypatch.setattr(os, "kill", _fake_kill)
    with pytest.raises(ProcessLookupError):
        send_sigterm(999_999)


async def test_cancel_job_writes_command_file(tmp_path: Path) -> None:
    """cancel_job writes the cancel command file even when no PID file exists."""
    job_dir = tmp_path / "jobs" / "test-job"
    job_dir.mkdir(parents=True)

    # No PID file — cancel_job should write the command file and return early
    await cancel_job("test-job", root=tmp_path, timeout=0.1)

    action_path = paths.job_human_action("test-job", root=tmp_path)
    assert action_path.exists()
    payload = json.loads(action_path.read_text())
    assert payload["command"] == "cancel"
