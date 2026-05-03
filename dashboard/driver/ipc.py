"""IPC helpers: signal-based and command-file-based cancellation.

Per design doc § Communication patterns:
- *Dashboard → Job Driver* is rare. When it does happen (cancellation, human
  action injection, configuration update), the dashboard either sends a Unix
  signal (SIGTERM for cancellation) or writes a small command file the Job
  Driver polls.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path

from shared import paths
from shared.atomic import atomic_write_text

# ---------------------------------------------------------------------------
# Command-file writes
# ---------------------------------------------------------------------------


def write_cancel_command(
    job_slug: str,
    *,
    root: Path | None = None,
    reason: str = "human",
) -> None:
    """Write ``human-action.json`` with a ``cancel`` command.

    The Job Driver polls this file every ``COMMAND_POLL_INTERVAL`` seconds and
    raises a cancellation on discovery.
    """
    action_path = paths.job_human_action(job_slug, root=root)
    payload = json.dumps({"command": "cancel", "reason": reason})
    atomic_write_text(action_path, payload)


# ---------------------------------------------------------------------------
# Signal-based cancellation
# ---------------------------------------------------------------------------


def send_sigterm(pid: int) -> None:
    """Send SIGTERM to the given PID.

    Raises ``ProcessLookupError`` if the process no longer exists.
    """
    os.kill(pid, signal.SIGTERM)


async def cancel_job(
    job_slug: str,
    *,
    root: Path | None = None,
    timeout: float = 5.0,
) -> None:
    """Cancel a running Job Driver.

    Writes the cancel command file *and* sends SIGTERM to the PID recorded in
    ``job-driver.pid``. Waits up to *timeout* seconds for the process to exit.

    Parameters
    ----------
    job_slug:
        Slug of the job to cancel.
    root:
        Override for HAMMOCK_ROOT.
    timeout:
        Seconds to wait for graceful exit before giving up.
    """
    write_cancel_command(job_slug, root=root)

    pid_path = paths.job_driver_pid(job_slug, root=root)
    if not pid_path.exists():
        return

    try:
        raw = pid_path.read_text().strip()
        pid = int(raw)
    except (ValueError, OSError):
        return

    try:
        send_sigterm(pid)
    except (ProcessLookupError, PermissionError):
        return

    # Poll until the process exits or timeout
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            os.kill(pid, 0)  # check if process is alive
        except ProcessLookupError:
            break
        await asyncio.sleep(0.1)
