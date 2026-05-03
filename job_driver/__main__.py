"""Entry point: ``python -m job_driver <job_slug> [--root <path>] --fake-fixtures <dir>``.

Invoked by ``dashboard.driver.lifecycle.spawn_driver``. Runs until the job
reaches a terminal state (COMPLETED, ABANDONED, FAILED), is blocked on a
human (BLOCKED_ON_HUMAN), or until SIGTERM.

Stage 4 only ships ``FakeStageRunner``. The CLI **requires**
``--fake-fixtures`` for now so a misconfigured spawn fails before any job
state transition. Stage 5 introduces ``RealStageRunner`` (and a flag to
select it).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from job_driver.runner import JobDriver
from job_driver.stage_runner import FakeStageRunner


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="job_driver")
    parser.add_argument("job_slug", help="Job slug to execute.")
    parser.add_argument("--root", default=None, help="Override HAMMOCK_ROOT path.")
    parser.add_argument(
        "--fake-fixtures",
        default=None,
        help="Path to fake-stage fixture dir (FakeStageRunner). REQUIRED in Stage 4.",
    )
    args = parser.parse_args()

    if not args.fake_fixtures:
        # Refuse to start without a runner — would otherwise crash on the
        # first stage and leave the job stuck in STAGES_RUNNING.
        print(
            "error: --fake-fixtures <dir> is required in Stage 4 "
            "(no real stage runner exists yet; Stage 5 adds one).",
            file=sys.stderr,
        )
        sys.exit(2)

    root = Path(args.root).expanduser().resolve() if args.root else None
    stage_runner = FakeStageRunner(Path(args.fake_fixtures).expanduser().resolve())

    _setup_logging()
    driver = JobDriver(args.job_slug, root=root, stage_runner=stage_runner)
    asyncio.run(driver.run())


if __name__ == "__main__":
    main()
