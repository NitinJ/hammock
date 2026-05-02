"""Manual smoke test for Stage 8 — FastAPI shell + cache wiring.

Starts the dashboard server against a temp hammock root, hits
``GET /api/health``, verifies the response, then sends SIGTERM and waits
for clean shutdown within 3 s.

Run with::

    uv run python scripts/manual-smoke-stage08.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

PORT = 18765
URL = f"http://127.0.0.1:{PORT}/api/health"


def _wait_for_server(url: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except Exception:
            time.sleep(0.1)
    raise TimeoutError(f"Server did not start within {timeout}s")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="hammock-smoke-08-") as root:
        env = {**os.environ, "HAMMOCK_ROOT": root, "HAMMOCK_PORT": str(PORT)}
        proc = subprocess.Popen(
            [sys.executable, "-m", "dashboard"],
            env=env,
            cwd=str(Path(__file__).parent.parent),
        )
        try:
            print(f"  server PID {proc.pid} starting on port {PORT}…")
            _wait_for_server(URL)
            print("  server up — hitting /api/health")

            with urllib.request.urlopen(URL, timeout=5) as resp:
                body = json.loads(resp.read())

            assert resp.status == 200, f"expected 200, got {resp.status}"
            assert body["ok"] is True, f"expected ok=true, got {body}"
            assert isinstance(body["cache_size"], int), f"cache_size not int: {body}"
            assert body["cache_size"] == 0, f"expected 0 entries, got {body['cache_size']}"

            print(f"  /api/health → {body}  ✓")

            # Graceful shutdown
            print("  sending SIGTERM…")
            t0 = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=3)
            elapsed = time.monotonic() - t0
            print(f"  server exited in {elapsed:.2f}s  ✓")
            assert elapsed < 3, f"shutdown took {elapsed:.2f}s (>3s)"

        finally:
            if proc.returncode is None:
                proc.kill()

    print("\nSmoke test PASSED")


if __name__ == "__main__":
    main()
