"""Lifecycle helpers: spawn the v1 engine driver subprocess.

Per impl-patch §Stage 5: ``spawn_driver`` shells out to
``python -m engine.v1 <job_slug> [--root <path>]``. The job dir must
already exist (created by ``dashboard.compiler.compile.compile_job``).

The subprocess is double-forked so the grandchild detaches fully from
the dashboard process; on success the grandchild PID is written to
``jobs/<slug>/job-driver.pid`` and returned.
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
    claude_binary: str | None = None,
    python: str | None = None,
) -> int:
    """Spawn ``engine.v1`` as a fully detached subprocess; return its PID.

    Args:
        job_slug: slug of the v1 job whose dir already exists on disk.
        root: HAMMOCK_ROOT override passed through ``--root``.
        fake_fixtures_dir: kept for API compat; ignored — v1 has no
                           fake-fixtures runner mode (the tests/integration
                           harness uses FakeEngine directly).
        claude_binary: path to the ``claude`` CLI; if set, exported as
                       ``HAMMOCK_CLAUDE_BINARY`` for the driver subprocess.
        python: Python interpreter (defaults to ``sys.executable``).
    """
    py = python or sys.executable
    cmd = [py, "-m", "engine.v1", job_slug]
    if root is not None:
        cmd += ["--root", str(root)]

    env = dict(os.environ)
    if claude_binary is not None:
        env["HAMMOCK_CLAUDE_BINARY"] = claude_binary
    # fake_fixtures_dir is silently ignored in v1 — see docstring.
    _ = fake_fixtures_dir

    pid = _double_fork_exec(cmd, env)

    pid_path = paths.job_driver_pid(job_slug, root=root)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(pid_path, f"{pid}\n")

    return pid


def _double_fork_exec(cmd: list[str], env: dict[str, str]) -> int:
    """Double-fork ``cmd`` and return the grandchild PID.

    The grandchild is re-parented to init/PID 1 — it cannot zombie-leak
    into the dashboard even if the dashboard never reaps it. The
    intermediate child is reaped immediately."""
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
                    os.execvpe(cmd[0], cmd, env)
                except OSError:
                    os._exit(127)
            os.write(pipe_w, str(pid2).encode())
            os.close(pipe_w)
            os._exit(0)
        except BaseException:
            os._exit(1)

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
