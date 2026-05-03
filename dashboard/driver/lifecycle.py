"""Lifecycle helpers: spawn a Job Driver subprocess.

Per design doc § Process structure and implementation.md § Stage 4.

``spawn_driver`` is called after a successful ``compile_job`` (from the HTTP
``POST /api/jobs`` endpoint in Stage 14, and from the CLI in Stage 4 tests).

The subprocess runs ``python -m job_driver <slug> [--root <path>]``.
On success, the PID is written to ``jobs/<slug>/job-driver.pid`` and returned.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from shared import paths
from shared.atomic import atomic_write_text


async def spawn_driver(
    job_slug: str,
    *,
    root: Path | None = None,
    fake_fixtures_dir: Path | None = None,
    python: str | None = None,
) -> int:
    """Spawn ``job_driver`` as a detached subprocess; return its PID.

    Uses ``subprocess.Popen`` (not asyncio) for fire-and-forget semantics.
    The Job Driver survives dashboard restarts.

    Parameters
    ----------
    job_slug:
        Slug of the compiled job to execute.
    root:
        Override for HAMMOCK_ROOT passed via ``--root``.
    fake_fixtures_dir:
        If set, passes ``--fake-fixtures <dir>`` to use ``FakeStageRunner``.
    python:
        Python interpreter path (defaults to ``sys.executable``).
    """
    py = python or sys.executable
    cmd = [py, "-m", "job_driver", job_slug]

    if root is not None:
        cmd += ["--root", str(root)]
    if fake_fixtures_dir is not None:
        cmd += ["--fake-fixtures", str(fake_fixtures_dir)]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )

    pid = proc.pid

    # Detach — we don't own the process lifecycle
    proc.returncode = 0  # prevent ResourceWarning from Popen.__del__

    # Write PID file
    pid_path = paths.job_driver_pid(job_slug, root=root)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(pid_path, f"{pid}\n")

    return pid
