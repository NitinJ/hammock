"""CLI entry point that the dashboard spawns to run a job.

This wraps ``hammock_v2.engine.runner.run_job`` so the dashboard's
spawn helper can detach a subprocess that survives the request cycle.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
from pathlib import Path

from hammock_v2.engine.runner import JobConfig, run_job

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


def _fake_runner(
    cmd: list[str], cwd: Path, stdout_path: Path, stderr_path: Path
) -> subprocess.CompletedProcess[bytes]:
    """Stub runner used when HAMMOCK_V2_RUNNER_MODE=fake.

    Writes a one-line stream-json system message and returns 0 — useful
    for smoke testing the dashboard plumbing without burning tokens.
    """
    stdout_path.write_text(
        '{"type":"system","subtype":"init","cwd":"' + str(cwd) + '"}\n'
        '{"type":"result","subtype":"success","is_error":false,"result":"fake","num_turns":1,'
        '"total_cost_usd":0.0}\n'
    )
    stderr_path.write_text("")
    return subprocess.CompletedProcess(args=cmd, returncode=0)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--slug", required=True)
    p.add_argument("--workflow", required=True)
    p.add_argument("--request", required=True)
    p.add_argument("--root", required=True)
    p.add_argument("--project-repo-path", default=None)
    p.add_argument("--claude-binary", default="claude")
    p.add_argument("--runner-mode", default="real", choices=["real", "fake"])
    args = p.parse_args()

    project = Path(args.project_repo_path) if args.project_repo_path else None
    job = JobConfig(
        slug=args.slug,
        workflow_name=args.workflow,
        request_text=args.request,
        project_repo_path=project,
    )
    runner = _fake_runner if args.runner_mode == "fake" else None
    if runner is None:
        rc = run_job(job=job, root=Path(args.root), claude_binary=args.claude_binary)
    else:
        rc = run_job(job=job, root=Path(args.root), runner=runner)
    log.info("orchestrator subprocess returned rc=%s for %s", rc, args.slug)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
