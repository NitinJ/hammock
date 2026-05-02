"""Entry point: ``python -m hammock.dashboard``.

Starts a single-worker uvicorn server (per design doc § Process structure:
single worker is locked — multiple workers would split the cache).
"""

from __future__ import annotations

import logging

import uvicorn
from rich.logging import RichHandler

from dashboard.app import create_app
from dashboard.settings import Settings


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


def main() -> None:
    _configure_logging()
    settings = Settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1)


if __name__ == "__main__":
    main()
