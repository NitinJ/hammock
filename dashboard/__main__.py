"""Entry point: ``python -m hammock.dashboard``.

Starts a single-worker uvicorn server (per design doc § Process structure:
single worker is locked — multiple workers would split the cache).
"""

from __future__ import annotations

import uvicorn

from dashboard.app import _configure_logging, create_app
from dashboard.settings import Settings


def main() -> None:
    _configure_logging()
    settings = Settings()
    app = create_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, workers=1)


if __name__ == "__main__":
    main()
