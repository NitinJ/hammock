"""Manual smoke test for Stage 9 — HTTP API read endpoints.

Bootstraps a fixture hammock-root with three projects, five jobs, twelve
stages, and four HIL items, starts the dashboard server, hits every read
endpoint, and asserts the responses match expectations.

Run with::

    uv run python scripts/manual-smoke-stage09.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Make ``shared`` importable when invoked from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from shared import paths
from shared.atomic import atomic_append_jsonl, atomic_write_json
from shared.models import (
    AskQuestion,
    Event,
    HilItem,
    JobConfig,
    JobState,
    ProjectConfig,
    ReviewQuestion,
    StageRun,
    StageState,
)

PORT = 18766
BASE = f"http://127.0.0.1:{PORT}"
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _ts(off: int) -> datetime:
    return NOW + timedelta(minutes=off)


def _bootstrap(root: Path) -> None:
    # 3 projects
    for slug, status in [("alpha", "pass"), ("beta", "warn"), ("gamma", None)]:
        atomic_write_json(
            paths.project_json(slug, root=root),
            ProjectConfig(
                slug=slug,
                name=slug,
                repo_path=f"/tmp/{slug}",
                default_branch="main",
                created_at=NOW,
                last_health_check_at=NOW if status else None,
                last_health_check_status=status,  # type: ignore[arg-type]
            ),
        )

    # 5 jobs
    jobs = [
        ("alpha-job-1", "alpha", JobState.STAGES_RUNNING, 0),
        ("alpha-job-2", "alpha", JobState.COMPLETED, 10),
        ("beta-job-1", "beta", JobState.SUBMITTED, 20),
        ("beta-job-2", "beta", JobState.FAILED, 30),
        ("gamma-job-1", "gamma", JobState.STAGES_RUNNING, 40),
    ]
    for slug, project, state, off in jobs:
        atomic_write_json(
            paths.job_json(slug, root=root),
            JobConfig(
                job_id=f"id-{slug}",
                job_slug=slug,
                project_slug=project,
                job_type="fix-bug",
                created_at=_ts(off),
                created_by="smoke",
                state=state,
            ),
        )

    # 12 stages spread across the running jobs
    stage_specs: list[tuple[str, str, StageState, int, float]] = [
        ("alpha-job-1", "design", StageState.SUCCEEDED, 1, 1.0),
        ("alpha-job-1", "implement", StageState.RUNNING, 5, 0.5),
        ("alpha-job-1", "review", StageState.ATTENTION_NEEDED, 6, 0.1),
        ("alpha-job-1", "test", StageState.PENDING, 7, 0.0),
        ("alpha-job-2", "done-1", StageState.SUCCEEDED, 11, 0.4),
        ("alpha-job-2", "done-2", StageState.SUCCEEDED, 12, 0.3),
        ("beta-job-1", "design", StageState.READY, 21, 0.0),
        ("beta-job-2", "design", StageState.FAILED, 31, 0.2),
        ("beta-job-2", "implement", StageState.CANCELLED, 32, 0.1),
        ("gamma-job-1", "design", StageState.RUNNING, 41, 0.5),
        ("gamma-job-1", "implement", StageState.PENDING, 42, 0.0),
        ("gamma-job-1", "review", StageState.PENDING, 43, 0.0),
    ]
    for job, sid, state, off, cost in stage_specs:
        atomic_write_json(
            paths.stage_json(job, sid, root=root),
            StageRun(
                stage_id=sid,
                attempt=1,
                state=state,
                started_at=_ts(off),
                cost_accrued=cost,
            ),
        )

    # 4 HIL items (one per kind plus an extra ask)
    hil_items = [
        ("hil-ask-1", "alpha-job-1", "ask", "design", "Argon2id?", 2),
        ("hil-ask-2", "alpha-job-1", "ask", "implement", "Switch DB driver?", 6),
        ("hil-rev-1", "beta-job-1", "review", "design", None, 21),
        ("hil-ask-3", "gamma-job-1", "ask", "design", "Use feature flags?", 41),
    ]
    for item_id, job, kind, stage, text, off in hil_items:
        if kind == "ask":
            assert text is not None
            q = AskQuestion(text=text)
        else:
            q = ReviewQuestion(target="design-spec.md", prompt="Approve?")  # type: ignore[assignment]
        atomic_write_json(
            paths.hil_item_path(job, item_id, root=root),
            HilItem(
                id=item_id,
                kind=kind,  # type: ignore[arg-type]
                stage_id=stage,
                created_at=_ts(off),
                status="awaiting",
                question=q,
            ),
        )

    # Cost events on alpha-job-1
    job_events = paths.job_events_jsonl("alpha-job-1", root=root)
    for seq, sid, usd, agent in [
        (1, "design", 0.6, "design-spec-writer"),
        (2, "design", 0.4, "design-spec-writer"),
        (3, "implement", 1.0, "implementer"),
    ]:
        atomic_append_jsonl(
            job_events,
            Event(
                seq=seq,
                timestamp=_ts(seq + 1),
                event_type="cost_accrued",
                source="agent0",
                job_id="id-alpha-job-1",
                stage_id=sid,
                payload={"usd": usd, "tokens": 10000, "agent_ref": agent},
            ),
        )

    # An artifact under alpha-job-1
    (paths.job_dir("alpha-job-1", root=root) / "design-spec.md").write_text(
        "# alpha-job-1 design spec\n\nExample content.\n"
    )


def _wait_for(url: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return
        except Exception:
            time.sleep(0.1)
    raise TimeoutError(f"Server did not start within {timeout}s")


def _get(path: str) -> tuple[int, dict | list | str]:
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        body = resp.read().decode("utf-8")
        ct = resp.headers.get("content-type", "")
        if "application/json" in ct:
            return resp.status, json.loads(body)
        return resp.status, body


def _expect(name: str, status: int, want: int) -> None:
    if status != want:
        raise AssertionError(f"{name}: expected {want}, got {status}")
    print(f"  {name}  status={status}  ✓")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="hammock-smoke-09-") as root:
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
            print("  server up — exercising read endpoints")

            # health
            status, body = _get("/api/health")
            _expect("/api/health", status, 200)
            assert isinstance(body, dict)
            assert body["cache_size"] == 3 + 5 + 12 + 4, body

            # projects list
            t0 = time.monotonic()
            status, body = _get("/api/projects")
            elapsed = (time.monotonic() - t0) * 1000
            _expect("/api/projects", status, 200)
            assert isinstance(body, list)
            assert len(body) == 3, body
            assert elapsed < 50, f"projects list took {elapsed:.1f}ms (>50ms)"

            # project detail
            status, body = _get("/api/projects/alpha")
            _expect("/api/projects/alpha", status, 200)
            assert isinstance(body, dict)
            assert body["total_jobs"] == 2, body

            # 404 paths
            for path in [
                "/api/projects/nope",
                "/api/jobs/nope",
                "/api/jobs/alpha-job-1/stages/missing",
                "/api/hil/missing",
            ]:
                req = urllib.request.Request(BASE + path)
                try:
                    urllib.request.urlopen(req, timeout=5)
                except urllib.error.HTTPError as e:
                    _expect(path, e.code, 404)

            # jobs list + filters
            status, body = _get("/api/jobs")
            _expect("/api/jobs", status, 200)
            assert isinstance(body, list)
            assert len(body) == 5, body

            status, body = _get("/api/jobs?project=alpha")
            _expect("/api/jobs?project=alpha", status, 200)
            assert isinstance(body, list)
            assert len(body) == 2, body

            status, body = _get("/api/jobs?status=COMPLETED")
            _expect("/api/jobs?status=COMPLETED", status, 200)
            assert isinstance(body, list)
            assert {j["job_slug"] for j in body} == {"alpha-job-2"}

            # job detail
            status, body = _get("/api/jobs/alpha-job-1")
            _expect("/api/jobs/alpha-job-1", status, 200)
            assert isinstance(body, dict)
            assert body["total_cost_usd"] == 2.0, body
            assert len(body["stages"]) == 4, body

            # stage detail
            status, body = _get("/api/jobs/alpha-job-1/stages/implement")
            _expect("/api/jobs/alpha-job-1/stages/implement", status, 200)
            assert isinstance(body, dict)
            assert body["stage"]["state"] == "RUNNING", body

            # active stages
            status, body = _get("/api/active-stages")
            _expect("/api/active-stages", status, 200)
            assert isinstance(body, list)
            states = {s["state"] for s in body}
            assert states == {"RUNNING", "ATTENTION_NEEDED"}, body

            # HIL queue
            status, body = _get("/api/hil")
            _expect("/api/hil", status, 200)
            assert isinstance(body, list)
            assert len(body) == 4, body

            status, body = _get("/api/hil?kind=review")
            _expect("/api/hil?kind=review", status, 200)
            assert isinstance(body, list)
            assert len(body) == 1, body

            status, body = _get("/api/hil/hil-ask-1")
            _expect("/api/hil/hil-ask-1", status, 200)
            assert isinstance(body, dict)
            assert body["kind"] == "ask"

            # artifacts
            status, body = _get("/api/artifacts/alpha-job-1/design-spec.md")
            _expect("/api/artifacts/alpha-job-1/design-spec.md", status, 200)
            assert isinstance(body, str)
            assert "design spec" in body

            # costs
            status, body = _get("/api/costs?scope=job&id=alpha-job-1")
            _expect("/api/costs?scope=job&id=alpha-job-1", status, 200)
            assert isinstance(body, dict)
            assert body["total_usd"] == 2.0, body

            status, body = _get("/api/costs?scope=project&id=alpha")
            _expect("/api/costs?scope=project&id=alpha", status, 200)
            assert isinstance(body, dict)
            assert body["total_usd"] == 2.0, body

            # observatory stub
            status, body = _get("/api/observatory/metrics")
            _expect("/api/observatory/metrics", status, 200)

            # openapi spec is consumable
            status, body = _get("/openapi.json")
            _expect("/openapi.json", status, 200)
            assert isinstance(body, dict)
            assert "/api/projects" in body["paths"], "openapi missing routes"

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
