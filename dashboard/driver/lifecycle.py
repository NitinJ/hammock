"""Lifecycle helpers: spawn a Job Driver subprocess.

Per design doc § Process structure and implementation.md § Stage 4.

``spawn_driver`` is called after a successful ``compile_job`` (from the HTTP
``POST /api/jobs`` endpoint in Stage 14, and from the CLI in Stage 4 tests).

The subprocess runs ``python -m job_driver <slug> [--root <path>]
--fake-fixtures <dir>``. We use a **double-fork** to fully detach the
grandchild from the dashboard process: the grandchild is re-parented to
init/PID 1, so it can never become a zombie of the dashboard, even if the
dashboard never reaps it. The intermediate child is reaped immediately by
the dashboard.

On success, the grandchild PID is written to ``jobs/<slug>/job-driver.pid``
and returned.
"""

from __future__ import annotations

import os
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
    """Spawn ``job_driver`` as a fully detached subprocess; return its PID.

    Detachment is achieved via a POSIX double-fork. The Job Driver survives
    dashboard restarts.

    Parameters
    ----------
    job_slug:
        Slug of the compiled job to execute.
    root:
        Override for HAMMOCK_ROOT passed via ``--root``.
    fake_fixtures_dir:
        Required in Stage 4 (passed via ``--fake-fixtures <dir>``). Stage 5
        will allow ``None`` once the real runner exists.
    python:
        Python interpreter path (defaults to ``sys.executable``).
    """
    py = python or sys.executable
    cmd = [py, "-m", "job_driver", job_slug]

    if root is not None:
        cmd += ["--root", str(root)]
    if fake_fixtures_dir is not None:
        cmd += ["--fake-fixtures", str(fake_fixtures_dir)]

    pid = _double_fork_exec(cmd)

    pid_path = paths.job_driver_pid(job_slug, root=root)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(pid_path, f"{pid}\n")

    return pid


def _double_fork_exec(cmd: list[str]) -> int:
    """Double-fork ``cmd`` and return the grandchild PID.

    Sequence:
      1. Parent forks; first child does ``setsid()`` so it leaves the
         dashboard's process group.
      2. First child forks again; the grandchild ``execvp()``s the command.
      3. First child writes the grandchild PID to a pipe and ``_exit(0)``s.
      4. Parent ``waitpid()``s the first child immediately (so it never
         becomes a zombie) and reads the grandchild PID from the pipe.

    The grandchild is now an orphan (re-parented to init/PID 1) and will
    never zombie-leak into the dashboard.
    """
    pipe_r, pipe_w = os.pipe()
    pid = os.fork()
    if pid == 0:
        # First child
        os.close(pipe_r)
        try:
            os.setsid()
            pid2 = os.fork()
            if pid2 == 0:
                # Grandchild — redirect stdio and exec
                os.close(pipe_w)
                devnull = os.open(os.devnull, os.O_RDWR)
                os.dup2(devnull, 0)
                os.dup2(devnull, 1)
                os.dup2(devnull, 2)
                if devnull > 2:
                    os.close(devnull)
                try:
                    os.execvp(cmd[0], cmd)
                except OSError:
                    os._exit(127)
            # Intermediate child — report grandchild PID and exit
            os.write(pipe_w, str(pid2).encode())
            os.close(pipe_w)
            os._exit(0)
        except BaseException:
            os._exit(1)

    # Parent: reap the intermediate child and read the grandchild PID
    os.close(pipe_w)
    try:
        os.waitpid(pid, 0)
        chunks: list[bytes] = []
        while True:
            buf = os.read(pipe_r, 64)
            if not buf:
                break
            chunks.append(buf)
        pid_str = b"".join(chunks).decode().strip()
    finally:
        os.close(pipe_r)

    if not pid_str:
        raise RuntimeError("spawn_driver: failed to read grandchild PID")
    return int(pid_str)
