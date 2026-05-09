"""Spawn the v2 engine runner in a background subprocess.

We don't keep handles. The orchestrator writes everything to disk;
the dashboard reads from disk; if the dashboard restarts, in-flight
runs keep going until the orchestrator subprocess exits naturally.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from hammock.engine import paths

log = logging.getLogger(__name__)


def spawn_orchestrator(
    *,
    slug: str,
    workflow_name: str,
    request_text: str,
    root: Path,
    project_repo_path: Path | None,
    claude_binary: str,
    runner_mode: str = "real",
    workflow_path: Path | None = None,
) -> int:
    """Detach a python child that runs hammock.engine.runner.run_job.

    Returns the child PID. Output goes to <job_dir>/orchestrator.{jsonl,log}
    via the runner itself.
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    args = [
        sys.executable,
        "-m",
        "dashboard.runner.run_job",
        "--slug",
        slug,
        "--workflow",
        workflow_name,
        "--request",
        request_text,
        "--root",
        str(root),
        "--claude-binary",
        claude_binary,
        "--runner-mode",
        runner_mode,
    ]
    if project_repo_path is not None:
        args += ["--project-repo-path", str(project_repo_path)]
    if workflow_path is not None:
        args += ["--workflow-path", str(workflow_path)]
    log.info("spawning orchestrator subprocess for %s", slug)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        args,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Persist the wrapper pid so the stop endpoint can SIGTERM the
    # process group. The wrapper cleans this up on its own exit.
    pid_path = paths.orchestrator_pid_file(slug, root=root)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(proc.pid))
    return proc.pid
