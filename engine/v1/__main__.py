"""``python -m engine.v1 <job_slug> [--root <path>]`` entry point.

Per impl-patch §Stage 5: the dashboard's lifecycle.spawn_driver
shells out to this module to run a v1 job to terminal state. The job
must already exist on disk (created by ``engine.v1.driver.submit_job``
or the dashboard's compile endpoint).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from engine.v1.driver import DriverError, run_job
from shared.v1 import paths as v1_paths

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="engine.v1")
    parser.add_argument("job_slug")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    root = args.root if args.root is not None else _default_root()
    log_path = v1_paths.job_dir(args.job_slug, root=root) / "driver.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(file_handler)

    try:
        cfg = run_job(job_slug=args.job_slug, root=root)
    except DriverError as exc:
        log.error("driver error: %s", exc)
        return 2
    log.info("job %s reached terminal state %s", args.job_slug, cfg.state.value)
    return 0


def _default_root() -> Path:
    from shared.paths import HAMMOCK_ROOT

    return HAMMOCK_ROOT


if __name__ == "__main__":
    raise SystemExit(main())
