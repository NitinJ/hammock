"""Run the dashboard via `python -m dashboard`."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("HAMMOCK_HOST", "127.0.0.1")
    port = int(os.environ.get("HAMMOCK_PORT", "8765"))
    uvicorn.run(
        "dashboard.api.app:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
