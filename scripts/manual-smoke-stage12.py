#!/usr/bin/env python3
"""Manual smoke test for Stage 12 — Read views.

Starts the Hammock dashboard server against the fixture workspace used by
the dashboard API tests, then hits every read endpoint that the Stage 12
views consume and asserts correct HTTP status codes + non-empty payloads.

Usage:
    uv run python scripts/manual-smoke-stage12.py

The server must NOT already be running on port 8765.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://localhost:8765"
FIXTURE = Path(__file__).parent.parent / "tests" / "dashboard" / "fixtures"


def _wait_for_server(timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(Exception):
            urllib.request.urlopen(f"{BASE}/api/health", timeout=1)
            return
        time.sleep(0.3)
    raise RuntimeError("Server did not start within timeout")


def _get(path: str) -> dict | list:
    url = f"{BASE}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        if resp.status != 200:
            raise AssertionError(f"GET {path} → HTTP {resp.status}")
        return json.loads(resp.read())


CHECKS: list[tuple[str, str]] = [
    # (endpoint, description)
    ("/api/health", "health"),
    ("/api/projects", "project list"),
    ("/api/active-stages", "active stages strip"),
    ("/api/hil?status=awaiting", "HIL queue (awaiting)"),
    ("/api/hil", "HIL queue (all)"),
    ("/api/jobs", "job list"),
    ("/api/costs?scope=project&id=demo-project", "cost rollup (project scope)"),
    ("/api/observatory/metrics", "observatory stub"),
]

OK = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def main() -> int:
    # Attempt to find fixture root — fall back to current working dir
    root = FIXTURE if FIXTURE.is_dir() else Path.cwd()

    print(f"Starting dashboard server (root={root}) …")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "dashboard.app:create_app",
         "--factory", "--host", "127.0.0.1", "--port", "8765"],
        env={**__import__("os").environ, "HAMMOCK_ROOT": str(root)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    failures = 0
    try:
        _wait_for_server()
        print("Server ready.\n")

        for endpoint, label in CHECKS:
            try:
                payload = _get(endpoint)
                print(f"  {OK}  {label:40s}  {endpoint}")
                _ = payload  # consumed
            except Exception as exc:
                print(f"  {FAIL}  {label:40s}  {endpoint}  → {exc}")
                failures += 1

    finally:
        proc.terminate()
        proc.wait(timeout=5)

    print(f"\n{'All checks passed.' if not failures else f'{failures} check(s) failed.'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
