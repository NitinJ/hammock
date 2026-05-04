"""Entry point: ``python -m job_driver <job_slug> [--root <path>] [--fake-fixtures <dir>]``.

Invoked by ``dashboard.driver.lifecycle.spawn_driver``. Runs until the job
reaches a terminal state (COMPLETED, ABANDONED, FAILED), is blocked on a
human (BLOCKED_ON_HUMAN), or until SIGTERM.

Runner selection:

- ``--fake-fixtures <dir>`` set → ``FakeStageRunner`` (deterministic
  fixture-driven runs; used by the lifecycle test, the bundled smoke,
  and operators who want to exercise the pipeline without invoking
  ``claude``).
- ``--fake-fixtures`` **absent** → ``RealStageRunner``, which spawns a
  real ``claude`` subprocess per stage. The runner needs the project's
  on-disk repo path; we resolve it by reading ``job.json`` for the job
  slug, then ``project.json`` for that job's project slug. The
  ``claude`` binary defaults to ``claude`` in ``$PATH``; override with
  ``--claude-binary``.

P1 (real-claude e2e precondition track) wires four previously-missing
RealStageRunner kwargs through this entry point:

- ``mcp_manager`` — fresh ``MCPManager`` per driver process; agents
  get the full Hammock tool surface for the duration of the job.
- ``stop_hook_path`` — bundled ``hammock/hooks/validate-stage-exit.sh``
  so artifact validation runs at the end of every stage instead of
  being silently skipped.
- ``job_slug`` + ``hammock_root`` — required so the per-stage MCP
  server descriptor knows which job + which root it's serving.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from importlib.resources import files as _pkg_files
from pathlib import Path

from dashboard.mcp.manager import MCPManager
from job_driver.runner import JobDriver
from job_driver.stage_runner import FakeStageRunner, RealStageRunner, StageRunner
from shared import paths
from shared.models.job import JobConfig
from shared.models.project import ProjectConfig

# ``hammock`` is a namespace package (no __init__.py); resolve the
# bundled Stop-hook script via importlib.resources so tests + production
# both find it regardless of how Hammock is installed.
_BUNDLED_STOP_HOOK: Path = Path(str(_pkg_files("hammock") / "hooks" / "validate-stage-exit.sh"))


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _resolve_project_root(job_slug: str, root: Path | None) -> Path:
    """Read job.json → project_slug, then project.json → repo_path."""
    job_cfg = JobConfig.model_validate_json(paths.job_json(job_slug, root=root).read_text())
    project_cfg = ProjectConfig.model_validate_json(
        paths.project_json(job_cfg.project_slug, root=root).read_text()
    )
    return Path(project_cfg.repo_path)


def _build_runner(
    *,
    job_slug: str,
    root: Path | None,
    fake_fixtures: str | None,
    claude_binary: str,
) -> StageRunner:
    """Select the StageRunner for this driver invocation.

    Real-mode preflight: verify the ``claude`` binary is resolvable
    before constructing ``RealStageRunner``. Without this, a missing
    binary surfaces only after ``asyncio.create_subprocess_exec`` runs
    inside ``RealStageRunner.run()`` — by which time the driver is
    already detached with stderr redirected to ``/dev/null``, leaving
    the operator with a stuck job and a fail-stage event but no
    actionable error.
    """
    if fake_fixtures:
        return FakeStageRunner(Path(fake_fixtures).expanduser().resolve())
    project_root = _resolve_project_root(job_slug, root)
    if shutil.which(claude_binary) is None and not Path(claude_binary).is_file():
        raise FileNotFoundError(
            f"`claude` binary not found at {claude_binary!r} (not in $PATH "
            "and not a file). Install Claude Code, set HAMMOCK_CLAUDE_BINARY "
            "to the absolute path of the binary, or pass --claude-binary."
        )
    return RealStageRunner(
        project_root=project_root,
        claude_binary=claude_binary,
        mcp_manager=MCPManager(),
        stop_hook_path=_BUNDLED_STOP_HOOK,
        job_slug=job_slug,
        hammock_root=root,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="job_driver")
    parser.add_argument("job_slug", help="Job slug to execute.")
    parser.add_argument("--root", default=None, help="Override HAMMOCK_ROOT path.")
    parser.add_argument(
        "--fake-fixtures",
        default=None,
        help=(
            "Path to fake-stage fixture dir. When set, uses FakeStageRunner. "
            "When absent, uses RealStageRunner (real `claude` subprocess)."
        ),
    )
    parser.add_argument(
        "--claude-binary",
        default="claude",
        help="Path to the `claude` CLI (RealStageRunner only; default: 'claude').",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve() if args.root else None

    try:
        stage_runner = _build_runner(
            job_slug=args.job_slug,
            root=root,
            fake_fixtures=args.fake_fixtures,
            claude_binary=args.claude_binary,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(
            f"error: cannot resolve project root for job {args.job_slug!r}: {exc}",
            file=sys.stderr,
        )
        sys.exit(2)

    _setup_logging()
    driver = JobDriver(args.job_slug, root=root, stage_runner=stage_runner)
    asyncio.run(driver.run())


if __name__ == "__main__":
    main()
