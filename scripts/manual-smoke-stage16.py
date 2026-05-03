#!/usr/bin/env python3
"""Manual smoke test for Stage 16 — full lifecycle dry-run.

This is the smoke companion to ``tests/e2e/test_full_lifecycle.py``: it
exercises the full submit → drive → resolve-human-gates → COMPLETED loop
against a tmp hammock-root, with all stages backed by FakeStageRunner
fixtures. Useful as a quick local pre-flight before opening the PR.

Usage:
    uv run python scripts/manual-smoke-stage16.py

Output: a summary line per stage transition + a final PASS / FAIL banner.

Distinct from the automated test in two ways:
  - Always logs to stdout (the test captures and asserts).
  - Leaves the tmp hammock-root in place at the end so you can poke
    around in `$HAMMOCK_ROOT/jobs/<slug>/` afterwards.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

# Allow running from a fresh checkout: ensure the repo root is on sys.path.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.app import create_app  # noqa: E402
from dashboard.driver.lifecycle import spawn_driver  # noqa: E402
from dashboard.settings import Settings  # noqa: E402
from shared import paths  # noqa: E402
from shared.atomic import atomic_write_json  # noqa: E402
from shared.models import ProjectConfig  # noqa: E402
from shared.models.job import JobConfig, JobState  # noqa: E402
from shared.models.stage import StageRun, StageState  # noqa: E402

# Reuse the exact fixture payloads + human-gate plan from the automated test
# so this script and CI cannot drift.
from tests.e2e.test_full_lifecycle import (  # noqa: E402
    _AGENT_FIXTURES,
    _APPROVED_VERDICT,
    _HUMAN_GATES,
)


def _write_fakes(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for stage_id, payload in _AGENT_FIXTURES.items():
        (d / f"{stage_id}.yaml").write_text(yaml.safe_dump(payload))


def _register_project(root: Path, *, slug: str, repo: Path) -> ProjectConfig:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir(exist_ok=True)
    (repo / "CLAUDE.md").write_text("# fake project\n")
    p = ProjectConfig(
        slug=slug,
        name=slug,
        repo_path=str(repo),
        remote_url=f"https://github.com/example/{slug}",
        default_branch="main",
        created_at=datetime.now(UTC),
    )
    atomic_write_json(paths.project_json(slug, root=root), p)
    overrides = paths.project_overrides_root(repo)
    (overrides / "job-template-overrides").mkdir(parents=True, exist_ok=True)
    return p


async def _wait_for(root: Path, slug: str, accept: set[JobState], timeout: float) -> JobState:
    deadline = asyncio.get_event_loop().time() + timeout
    last: JobState | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            cfg = JobConfig.model_validate_json(paths.job_json(slug, root=root).read_text())
            last = cfg.state
            if last in accept:
                return last
        except (FileNotFoundError, ValueError):
            pass
        await asyncio.sleep(0.1)
    raise TimeoutError(f"job {slug} stuck at {last} after {timeout}s")


async def _wait_for_block_on(root: Path, slug: str, stage_id: str, timeout: float) -> None:
    """Poll the *expected* stage's stage.json until BLOCKED_ON_HUMAN.

    Mirrors the e2e test's _wait_for_block_on_stage; polling job.json
    alone races across re-spawns because job.json stays BLOCKED_ON_HUMAN
    from the previous block until the re-spawned driver writes
    STAGES_RUNNING.
    """
    sj_path = paths.stage_json(slug, stage_id, root=root)
    deadline = asyncio.get_event_loop().time() + timeout
    last: StageState | None = None
    while asyncio.get_event_loop().time() < deadline:
        if sj_path.exists():
            try:
                sj = StageRun.model_validate_json(sj_path.read_text())
                last = sj.state
                if last == StageState.BLOCKED_ON_HUMAN:
                    return
                if last in (StageState.FAILED, StageState.CANCELLED):
                    raise RuntimeError(
                        f"stage {stage_id} reached terminal {last.value} not BLOCKED_ON_HUMAN"
                    )
            except ValueError:
                pass
        await asyncio.sleep(0.1)
    raise TimeoutError(
        f"stage {stage_id} did not reach BLOCKED_ON_HUMAN within {timeout}s (last: {last})"
    )


def _resolve_human(root: Path, slug: str, stage_id: str, output_filename: str) -> None:
    job_dir = paths.job_dir(slug, root=root)
    (job_dir / output_filename).write_text(json.dumps(_APPROVED_VERDICT))
    sj = paths.stage_json(slug, stage_id, root=root)
    attempt = StageRun.model_validate_json(sj.read_text()).attempt if sj.exists() else 1
    atomic_write_json(
        sj,
        StageRun(
            stage_id=stage_id,
            attempt=attempt,
            state=StageState.SUCCEEDED,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            cost_accrued=0.0,
        ),
    )


async def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="hammock-stage16-smoke-"))
    print(f"[smoke] tmp root: {tmp}")
    root = tmp / "hammock-root"
    root.mkdir()
    fakes = tmp / "fakes"
    _write_fakes(fakes)
    _register_project(root, slug="smoke-target", repo=tmp / "repo")
    print("[smoke] registered project: smoke-target")

    settings = Settings(root=root, fake_fixtures_dir=fakes)
    app = create_app(settings)

    with TestClient(app) as client:
        resp = client.post(
            "/api/jobs",
            json={
                "project_slug": "smoke-target",
                "job_type": "fix-bug",
                "title": "smoke off-by-one",
                "request_text": "smoke test for the full lifecycle.",
            },
        )
        if resp.status_code != 201:
            print(f"[smoke] FAIL: submit returned {resp.status_code}: {resp.text}")
            return 1
        job_slug = resp.json()["job_slug"]
        print(f"[smoke] submitted: {job_slug}")

    terminal = {JobState.COMPLETED, JobState.FAILED, JobState.ABANDONED}
    for stage_id, output_filename in _HUMAN_GATES:
        await _wait_for_block_on(root, job_slug, stage_id, timeout=30.0)
        print(f"[smoke] BLOCKED_ON_HUMAN at {stage_id}")
        _resolve_human(root, job_slug, stage_id, output_filename)
        await spawn_driver(job_slug, root=root, fake_fixtures_dir=fakes)
        print(f"[smoke] resolved {stage_id} + re-spawned driver")

    final = await _wait_for(root, job_slug, terminal, timeout=30.0)
    if final != JobState.COMPLETED:
        print(f"[smoke] FAIL: final state {final.value} != COMPLETED")
        return 1
    summary = paths.job_dir(job_slug, root=root) / "summary.md"
    if not summary.exists():
        print("[smoke] FAIL: summary.md missing")
        return 1

    print(f"[smoke] PASS — job {job_slug} reached COMPLETED")
    print(f"[smoke] inspect on disk: {paths.job_dir(job_slug, root=root)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
