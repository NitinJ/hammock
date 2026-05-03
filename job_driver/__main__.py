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

The real-runner path here does not yet wire the per-stage MCP server
(Stage 6 ``MCPManager``) or the Stop hook script. Both are implemented
inside ``RealStageRunner``; passing them from this entry point is a
follow-up. Today the real path runs ``claude`` with the bundled
session settings only, so MCP tools available to agents land in a
later wiring stage.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from job_driver.runner import JobDriver
from job_driver.stage_runner import FakeStageRunner, RealStageRunner, StageRunner
from shared import paths
from shared.models.job import JobConfig
from shared.models.project import ProjectConfig


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
    """Pure runner-selection. Extracted from ``main`` so tests can exercise
    the choice + project-resolution logic without going through
    ``asyncio.run`` (which leaves event-loop debris that confuses
    pytest's unraisable-exception capture in long suites).
    """
    if fake_fixtures:
        return FakeStageRunner(Path(fake_fixtures).expanduser().resolve())
    project_root = _resolve_project_root(job_slug, root)
    return RealStageRunner(project_root=project_root, claude_binary=claude_binary)


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
