"""Codex review HIGH 1 — RealStageRunner must terminate its subprocess
when its enclosing task is cancelled (e.g. JobDriver wall-clock
watchdog wins). Without this the claude subprocess survives the cancel
and the budget cap is silently bypassed.

Two layers of test:

1. **Helper unit test** — directly exercise `_terminate_subprocess`
   against a real subprocess. Uses `subprocess.Popen` rather than
   `asyncio.create_subprocess_exec` so the helper's `os.waitpid` reap
   doesn't race asyncio's child watcher (which trips pytest's
   unraisable-exception capture even though no real bug exists).

2. **Production-call test** — spy on the helper to confirm
   ``RealStageRunner.run`` actually invokes it on `CancelledError`.
   This one *does* go through asyncio's subprocess transport, so the
   module-level filter below silences the benign teardown warning.
"""

from __future__ import annotations

import asyncio
import os
import stat
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from job_driver.stage_runner import RealStageRunner, _terminate_subprocess
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    StageDefinition,
)

# Suppress the asyncio-subprocess-transport teardown noise for this whole
# file. The warning is emitted by pytest's `unraisableexception` plugin
# in its teardown phase, after the test body finishes; per-test
# `@pytest.mark.filterwarnings` doesn't catch it. The actual contract
# (pid dead after cancel; helper invoked) is asserted directly.
pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnraisableExceptionWarning")


def _stage() -> StageDefinition:
    return StageDefinition(
        id="s",
        worker="agent",
        agent_ref="x",
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=[]),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(),
    )


def _slow_fake_claude(tmp_path: Path) -> Path:
    """Fake claude that records its PID then sleeps 30 s."""
    pid_file = tmp_path / "claude.pid"
    script = tmp_path / "slow_claude"
    script.write_text(f"#!/usr/bin/env bash\necho $$ > {pid_file}\nsleep 30\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# ---------------------------------------------------------------------------
# Helper unit test — _terminate_subprocess
# ---------------------------------------------------------------------------


class _ProcShim:
    """Duck-typed asyncio.subprocess.Process for the helper.

    The helper only uses .returncode, .pid, .send_signal, .kill — all
    of which subprocess.Popen also provides. Using Popen avoids racing
    asyncio's child watcher when our helper calls os.waitpid.
    """

    def __init__(self, p: subprocess.Popen) -> None:
        self.pid = p.pid
        self._p = p

    @property
    def returncode(self):
        return self._p.returncode

    def send_signal(self, sig):
        self._p.send_signal(sig)

    def kill(self):
        self._p.kill()


def test_terminate_subprocess_kills_a_running_process(tmp_path: Path) -> None:
    """Given a real subprocess, _terminate_subprocess() returns with the
    pid no longer alive."""
    p = subprocess.Popen(
        ["/bin/sh", "-c", "sleep 30"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        pid = p.pid
        assert _pid_alive(pid)
        _terminate_subprocess(_ProcShim(p), grace_seconds=2.0)  # type: ignore[arg-type]
        assert not _pid_alive(pid), f"subprocess (pid={pid}) survived termination"
    finally:
        if _pid_alive(p.pid):
            p.kill()
            p.wait()


def test_terminate_subprocess_is_noop_on_already_exited(tmp_path: Path) -> None:
    """Calling on an already-finished subprocess must not raise."""
    p = subprocess.Popen(["/bin/true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.wait()
    _terminate_subprocess(_ProcShim(p), grace_seconds=1.0)  # type: ignore[arg-type]


def test_terminate_subprocess_escalates_to_kill(tmp_path: Path) -> None:
    """A subprocess that ignores SIGTERM (`trap '' TERM`) must still be
    killed within grace + a short escalation window."""
    # Use blocking subprocess.Popen here — easier to assert SIGKILL
    # behaviour without juggling asyncio event-loop semantics. The
    # production helper uses asyncio.subprocess.Process but the kill
    # path is the same os.kill call.
    p = subprocess.Popen(
        ["/bin/sh", "-c", "trap '' TERM; sleep 30"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Build an asyncio.subprocess.Process-shaped wrapper just enough
        # for _terminate_subprocess to use it (returncode/pid/send_signal/kill).
        class _ProcShim:
            def __init__(self, p):
                self.pid = p.pid
                self._p = p

            @property
            def returncode(self):
                return self._p.returncode

            def send_signal(self, sig):
                self._p.send_signal(sig)

            def kill(self):
                self._p.kill()

        _terminate_subprocess(_ProcShim(p), grace_seconds=0.5)  # type: ignore[arg-type]
        # After grace expires, the helper SIGKILLs.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and _pid_alive(p.pid):
            time.sleep(0.05)
        assert not _pid_alive(p.pid), "stubborn subprocess survived SIGKILL escalation"
    finally:
        if _pid_alive(p.pid):
            p.kill()
            p.wait()


# ---------------------------------------------------------------------------
# Production-call test — RealStageRunner.run wires the helper
# ---------------------------------------------------------------------------


async def test_real_runner_calls_terminate_on_cancel(tmp_path: Path) -> None:
    """Cancelling the runner task must call _terminate_subprocess.

    Together with the helper tests above, this proves the end-to-end
    contract without depending on asyncio subprocess-transport
    teardown order (which has noisy GC interactions with pytest's
    unraisable-exception capture).
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run"
    stage_run_dir.mkdir()

    fake_claude = _slow_fake_claude(tmp_path)
    pid_file = tmp_path / "claude.pid"

    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))

    captured: dict = {"called": False, "pid": None}

    real_terminate = _terminate_subprocess

    def _spy(proc, *, grace_seconds):
        captured["called"] = True
        captured["pid"] = proc.pid
        real_terminate(proc, grace_seconds=grace_seconds)

    with patch("job_driver.stage_runner._terminate_subprocess", side_effect=_spy):
        runner_task = asyncio.create_task(runner.run(_stage(), tmp_path, stage_run_dir))

        # Wait for fake claude to write its pid (it does so immediately).
        deadline = asyncio.get_event_loop().time() + 3.0
        while asyncio.get_event_loop().time() < deadline and not pid_file.exists():
            await asyncio.sleep(0.05)
        assert pid_file.exists(), "fake claude never started"
        spawned_pid = int(pid_file.read_text().strip())

        runner_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner_task

    assert captured["called"], "RealStageRunner.run did not call _terminate_subprocess on cancel"
    # The helper was called for the actual subprocess, not some other process.
    assert captured["pid"] == spawned_pid
