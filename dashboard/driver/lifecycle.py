"""Lifecycle helpers: spawn a Job Driver subprocess.

Per design doc § Process structure and implementation.md § Stage 4.

``spawn_driver`` is called after a successful ``compile_job`` (from the HTTP
``POST /api/jobs`` endpoint in Stage 14, and from the CLI in Stage 4 tests).

The subprocess runs ``python -m job_driver <slug> [--root <path>]``.
On success, the PID is written to ``jobs/<slug>/job-driver.pid`` and returned.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from shared import paths


async def spawn_driver(
    job_slug: str,
    *,
    root: Path | None = None,
    fake_fixtures_dir: Path | None = None,
    python: str | None = None,
) -> int:
    """Spawn ``job_driver`` as a subprocess; return its PID.

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
    raise NotImplementedError
