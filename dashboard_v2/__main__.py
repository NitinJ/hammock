"""Run the v2 dashboard via `python -m dashboard_v2`."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("HAMMOCK_V2_HOST", "127.0.0.1")
    port = int(os.environ.get("HAMMOCK_V2_PORT", "8766"))
    uvicorn.run(
        "dashboard_v2.api.app:app",
        host=host,
        port=port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
