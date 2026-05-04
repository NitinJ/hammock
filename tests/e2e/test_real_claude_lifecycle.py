"""Real-claude end-to-end lifecycle test (closing PR of the precondition track).

Per docs/specs/2026-05-03-real-claude-e2e-test-design.md +
docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step I.

The test is **opt-in**: gated on both the ``real_claude`` pytest
marker and the ``HAMMOCK_E2E_REAL_CLAUDE=1`` env var. Default
``pytest`` invocations collect-and-skip via the preflight fixture.

Invoke explicitly:

    HAMMOCK_E2E_REAL_CLAUDE=1 pytest -m real_claude tests/e2e/

The test wires together steps C–H of the impl plan:

1. :func:`run_preflight` (D)  — env + tooling probe; skip-vs-fail (D12).
2. :func:`bootstrap_test_repo` (C) — create-or-reuse the test repo.
3. :func:`take_snapshot` (G)  — record pre-existing branches.
4. ``hammock project register`` + ``hammock job submit`` — production CLI path (D15).
5. Polling loop — when the driver blocks, :func:`stitch_hil_gate` (F)
   resolves it (writing artifacts via the BUILDERS registry, then
   POSTing to ``/api/hil/{id}/answer`` for record fidelity), and the
   driver is re-spawned.
6. :data:`OUTCOMES` (H) — every contract from spec §Outcomes asserted
   on the final job dir.
7. :func:`teardown` (G) — unconditional cleanup; always runs.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.driver.lifecycle import spawn_driver
from dashboard.settings import Settings
from shared import paths
from shared.models.job import JobConfig, JobState
from shared.models.stage import StageRun, StageState
from tests.e2e.cleanup import take_snapshot, teardown
from tests.e2e.hil_stitcher import stitch_hil_gate
from tests.e2e.outcomes import (
    OUTCOMES,
    assert_branches_exist,
)
from tests.e2e.preflight import (
    PreflightConfig,
    PreflightFailure,
    PreflightSkip,
    run_preflight,
)
from tests.e2e.repo_bootstrap import bootstrap_test_repo

_SEED_DIR = Path(__file__).parent / "seed_test_repo"


def _timeout_seconds() -> int:
    """Honour HAMMOCK_E2E_TIMEOUT_MIN early so the marker reads it.

    pytest-timeout reads the marker at collection time; the env var is
    parsed here so the wall-clock cap can be raised per-run without
    touching the test source.
    """
    raw = os.environ.get("HAMMOCK_E2E_TIMEOUT_MIN", "30")
    try:
        minutes = int(raw)
    except ValueError:
        minutes = 30
    return max(60, minutes * 60)


def _slug_from_url(url: str) -> str:
    if "://" not in url and url.count("/") == 1:
        return url
    from urllib.parse import urlparse

    path = urlparse(url).path.lstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return path


@pytest.mark.real_claude
@pytest.mark.timeout(_timeout_seconds())
@pytest.mark.asyncio
async def test_real_claude_full_lifecycle(tmp_path: Path) -> None:
    # 1. Preflight — skip when opt-in is unset; fail when opt-in is set
    #    but the rest is misconfigured (D12).
    try:
        cfg: PreflightConfig = run_preflight(env=os.environ)
    except PreflightSkip as exc:
        pytest.skip(str(exc))
    except PreflightFailure as exc:
        pytest.fail(str(exc))

    repo_slug = _slug_from_url(cfg.repo_url)

    # 2. Bootstrap (create-if-absent + seed + branch protection).
    bootstrap_test_repo(cfg.repo_url, seed_dir=_SEED_DIR)

    # 3. Snapshot pre-existing branches so teardown only deletes the diff.
    snapshot = take_snapshot(repo_slug)

    root = tmp_path / "hammock-root"
    root.mkdir()

    try:
        # 4. Register the project + submit the job through the CLI.
        project_slug = _register_project_via_cli(root, cfg.repo_url)
        job_slug = _submit_job_via_cli(root, project_slug=project_slug, job_type=cfg.job_type)

        # 5. Drive to terminal — stitch HIL gates, re-spawn driver,
        # poll on-disk state. pytest-timeout enforces wall-clock.
        settings = Settings(root=root, run_background_tasks=False)
        with TestClient(create_app(settings)) as app_client:
            await _drive_to_terminal(root=root, job_slug=job_slug, app_client=app_client)

            # 6. Outcome assertions — every spec contract.
            for fn in OUTCOMES.values():
                fn(root, job_slug)

            # Outcome #11 (branches present) — this is the assertion
            # that depends on GitHub credentials being plumbed into
            # the spawned claude subprocess. Hard fail by design (D11
            # of the spec / open-decision #2): a failure here is the
            # signal that project-config plumbing needs work.
            assert_branches_exist(
                repo_slug,
                list_remote_branches=lambda slug: _list_remote_branches(slug),
                job_slug=job_slug,
            )
    finally:
        # 7. Unconditional teardown.
        teardown(
            root=root,
            repo_slug=repo_slug,
            snapshot=snapshot,
            keep_root=cfg.keep_root,
        )


# ---------------------------------------------------------------------------
# CLI invocations — keep them flat & discoverable so a failing run is
# easy to reproduce by hand.
# ---------------------------------------------------------------------------


def _register_project_via_cli(root: Path, repo_url: str) -> str:
    """Run ``hammock project register`` against tmp root; return slug."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli",
            "--root",
            str(root),
            "project",
            "register",
            repo_url,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"hammock project register failed: rc={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    # The slug is the last path segment of the repo URL by convention.
    slug = _slug_from_url(repo_url).split("/")[-1]
    return slug


def _submit_job_via_cli(root: Path, *, project_slug: str, job_type: str) -> str:
    """Run ``hammock job submit`` against tmp root; return job_slug."""
    title = f"e2e {job_type}"
    request = (
        f"Hammock real-claude e2e test (auto-generated). Job type: {job_type}. "
        "Make any reasonable change consistent with the seed repo's "
        "add_integers.py + tests."
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli",
            "--root",
            str(root),
            "job",
            "submit",
            project_slug,
            "--type",
            job_type,
            "--title",
            title,
            "--request",
            request,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"hammock job submit failed: rc={result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    # The slug is printed by submit; parse it from stdout.
    slug = _parse_job_slug(result.stdout)
    if slug is None:
        raise AssertionError(f"could not parse job slug from submit output: {result.stdout!r}")
    return slug


def _parse_job_slug(stdout: str) -> str | None:
    """Best-effort: pick a token that looks like a job slug from CLI stdout.

    The CLI's exact output format is owned elsewhere; rely on the slug
    appearing on a line containing "job_slug" or as the last token of
    a "submitted" line. Falls back to scanning the jobs dir.
    """
    import re

    match = re.search(r"job[_-]?slug[\":\s=]+([a-z0-9-]+)", stdout, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    # Fallback: a line matching the slug format on its own.
    for line in stdout.splitlines():
        line = line.strip()
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{2,}", line):
            return line
    return None


# ---------------------------------------------------------------------------
# Drive-to-terminal loop
# ---------------------------------------------------------------------------


async def _drive_to_terminal(*, root: Path, job_slug: str, app_client: TestClient) -> None:
    """Poll job + stage state; stitch HIL gates as they appear; respawn
    the driver each time we resolve a gate. pytest-timeout enforces
    wall-clock at the test level."""
    poll_interval = 1.0
    while True:
        cfg = JobConfig.model_validate_json(paths.job_json(job_slug, root=root).read_text())
        if cfg.state in (JobState.COMPLETED, JobState.FAILED, JobState.ABANDONED):
            return

        if cfg.state == JobState.BLOCKED_ON_HUMAN:
            stage_id = _find_blocked_stage(root, job_slug)
            if stage_id is None:
                # Driver flipped job to BLOCKED_ON_HUMAN before writing
                # the per-stage marker; spin once.
                await asyncio.sleep(poll_interval)
                continue

            await stitch_hil_gate(
                root=root,
                job_slug=job_slug,
                stage_id=stage_id,
                app_client=app_client,  # type: ignore[arg-type]  # TestClient.post matches the _ClientLike protocol structurally
            )
            # Re-spawn the driver so it picks up the resolved gate.
            await spawn_driver(job_slug, root=root)

        await asyncio.sleep(poll_interval)


def _find_blocked_stage(root: Path, job_slug: str) -> str | None:
    stages_dir = paths.job_dir(job_slug, root=root) / "stages"
    if not stages_dir.is_dir():
        return None
    for sj_path in sorted(stages_dir.glob("*/stage.json")):
        try:
            sr = StageRun.model_validate_json(sj_path.read_text())
        except (OSError, ValueError):
            continue
        if sr.state == StageState.BLOCKED_ON_HUMAN:
            return sr.stage_id
    return None


# ---------------------------------------------------------------------------
# Remote-branch listing for the outcome #11 assertion
# ---------------------------------------------------------------------------


def _list_remote_branches(repo_slug: str) -> set[str]:
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo_slug}/branches",
            "--jq",
            ".[].name",
            "--paginate",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"gh api repos/{repo_slug}/branches failed: rc={result.returncode}\n"
            f"stderr={result.stderr}"
        )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}
