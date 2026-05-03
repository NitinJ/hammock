"""Manual smoke test for Stage 10 — SSE delivery + replay.

Bootstraps a minimal hammock-root with two events in a stage events.jsonl,
starts the dashboard server, and exercises the three SSE endpoints.

Run with::

    uv run python scripts/manual-smoke-stage10.py
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# Make ``shared`` importable when invoked from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared import paths
from shared.atomic import atomic_append_jsonl, atomic_write_json
from shared.models import Event, JobConfig, JobState, StageRun, StageState

PORT = 18767
BASE = f"http://127.0.0.1:{PORT}"
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

SLUG = "smoke-job"
STAGE = "design"


def _bootstrap(root: Path) -> None:
    # Minimal job
    atomic_write_json(
        paths.job_json(SLUG, root=root),
        JobConfig(
            job_id="id-smoke-job",
            job_slug=SLUG,
            project_slug="smoke-project",
            job_type="fix-bug",
            created_at=NOW,
            created_by="smoke",
            state=JobState.STAGES_RUNNING,
        ),
    )

    # Minimal stage
    atomic_write_json(
        paths.stage_json(SLUG, STAGE, root=root),
        StageRun(
            stage_id=STAGE,
            attempt=1,
            state=StageState.RUNNING,
            started_at=NOW,
            cost_accrued=0.0,
        ),
    )

    # Two cost_accrued events in the stage events.jsonl
    stage_events = paths.stage_events_jsonl(SLUG, STAGE, root=root)
    for seq in (1, 2):
        atomic_append_jsonl(
            stage_events,
            Event(
                seq=seq,
                timestamp=NOW,
                event_type="cost_accrued",
                source="agent0",
                job_id="id-smoke-job",
                stage_id=STAGE,
                payload={"usd": 0.1 * seq, "tokens": 1000 * seq, "agent_ref": "smoke"},
            ),
        )


def _wait_for(url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except Exception:
            time.sleep(0.1)
    raise TimeoutError(f"Server did not start within {timeout}s")


def _read_lines(url: str, *, headers: dict[str, str] | None = None, budget: int = 40) -> list[str]:
    """Open an SSE URL, read up to *budget* lines, return them."""
    req = urllib.request.Request(url, headers=headers or {})
    lines: list[str] = []
    with urllib.request.urlopen(req, timeout=5) as resp:
        for _ in range(budget):
            raw = resp.readline()
            if not raw:
                break
            lines.append(raw.decode("utf-8", errors="replace").rstrip("\n").rstrip("\r"))
    return lines


def _fail(name: str, msg: str) -> None:
    print(f"  FAIL  {name}: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="hammock-smoke-10-") as root:
        root_path = Path(root)
        _bootstrap(root_path)

        env = {**os.environ, "HAMMOCK_ROOT": root, "HAMMOCK_PORT": str(PORT)}
        proc = subprocess.Popen(
            [sys.executable, "-m", "dashboard"],
            env=env,
            cwd=str(Path(__file__).parent.parent),
        )
        try:
            print(f"  server PID {proc.pid} starting on port {PORT}…")
            _wait_for(BASE + "/api/health")
            print("  server up — exercising SSE endpoints")

            # ------------------------------------------------------------------
            # Test 1 — Content-type: GET /sse/global must return text/event-stream
            # ------------------------------------------------------------------
            try:
                req = urllib.request.Request(BASE + "/sse/global")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    ct = resp.headers.get("content-type", "")
                    if "text/event-stream" not in ct:
                        _fail("Test 1 (content-type)", f"got Content-Type: {ct!r}")
                print("  PASS  Test 1 — /sse/global Content-Type: text/event-stream")
            except Exception as exc:
                _fail("Test 1 (content-type)", str(exc))

            # ------------------------------------------------------------------
            # Test 2 — Replay: GET /sse/stage/<slug>/<stage> with Last-Event-ID: 0
            #           must stream back events with id: 1 and id: 2
            # ------------------------------------------------------------------
            try:
                url = f"{BASE}/sse/stage/{SLUG}/{STAGE}"
                lines = _read_lines(
                    url,
                    headers={"Last-Event-ID": "0"},
                    budget=40,
                )
                found_ids = {ln for ln in lines if ln.startswith("id:")}
                has_1 = any("1" in ln for ln in found_ids)
                has_2 = any("2" in ln for ln in found_ids)
                if not has_1:
                    _fail("Test 2 (replay)", f"id: 1 not found in lines: {lines!r}")
                if not has_2:
                    _fail("Test 2 (replay)", f"id: 2 not found in lines: {lines!r}")
                print("  PASS  Test 2 — /sse/stage replay: id: 1 and id: 2 present")
            except SystemExit:
                raise
            except Exception as exc:
                _fail("Test 2 (replay)", str(exc))

            # ------------------------------------------------------------------
            # Test 3 — No replay without header: GET /sse/stage/<slug>/<stage>
            #           (no Last-Event-ID) must NOT produce id: lines in first bytes
            # ------------------------------------------------------------------
            try:
                url = f"{BASE}/sse/stage/{SLUG}/{STAGE}"
                lines = _read_lines(url, headers={}, budget=10)
                id_lines = [ln for ln in lines if ln.startswith("id:")]
                if id_lines:
                    _fail(
                        "Test 3 (no replay without header)",
                        f"unexpected id: lines: {id_lines!r}",
                    )
                print("  PASS  Test 3 — /sse/stage without Last-Event-ID: no id: lines")
            except SystemExit:
                raise
            except Exception as exc:
                _fail("Test 3 (no replay without header)", str(exc))

            # Graceful shutdown
            print("  sending SIGTERM…")
            t0 = time.monotonic()
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=3)
            elapsed = time.monotonic() - t0
            print(f"  server exited in {elapsed:.2f}s  ✓")

        finally:
            if proc.returncode is None:
                proc.kill()

    print("\nSmoke test PASSED")


if __name__ == "__main__":
    main()
