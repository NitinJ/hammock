"""Entry point: ``python -m job_driver <job_slug> [--root <path>]``.

Invoked by ``dashboard.driver.lifecycle.spawn_driver``. Runs until the job
reaches a terminal state (COMPLETED, ABANDONED, FAILED) or until SIGTERM.
"""

from __future__ import annotations

import asyncio
import logging
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
        help="Path to fake-stage fixture dir (FakeStageRunner). Real driver only.",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve() if args.root else None
    stage_runner = None
    if args.fake_fixtures:
        stage_runner = FakeStageRunner(Path(args.fake_fixtures).expanduser().resolve())

    _setup_logging()
    driver = JobDriver(args.job_slug, root=root, stage_runner=stage_runner)
    asyncio.run(driver.run())


if __name__ == "__main__":
    main()
