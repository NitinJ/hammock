"""Stage 16 — full-lifecycle end-to-end test.

Covers the critical path: register a project, submit a ``fix-bug`` job, walk
through every stage in the bundled template, satisfy each human gate with an
``approved`` verdict, and confirm the job lands in ``COMPLETED`` with a
``summary.md`` artifact.

This test is **end-to-end** in the hammock sense: it exercises the real HTTP
``POST /api/jobs`` endpoint, the real Plan Compiler, the real
``spawn_driver`` (which runs ``python -m job_driver`` as a fully detached
grandchild process), and the real ``JobDriver`` state machine reading and
writing files in a tmp hammock-root. Stages execute via ``FakeStageRunner``
fed by per-stage YAML fixtures so the test runs deterministically in CI
without ``claude``, network, or a real GitHub remote.

It is **not** a frontend Playwright test: the v0 critical path lives in the
backend file pipeline, and the production HIL→artifact bridge that lets the
dashboard auto-resolve human gates is deferred to v1+. This test stitches
that wire by writing the human-gate output artifact and marking
``stage.json`` ``SUCCEEDED`` between driver re-spawns — exactly what a real
operator would do via the form pipeline once it ships.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.driver.lifecycle import spawn_driver
from dashboard.settings import Settings
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig
from shared.models.job import JobConfig, JobState
from shared.models.stage import StageRun, StageState

# ---------------------------------------------------------------------------
# Stage layout — must match hammock/templates/job-templates/fix-bug.yaml.
# Locked by the template, not by this test; if the template changes, this
# list and the per-stage fixtures below need to follow.
# ---------------------------------------------------------------------------

_AGENT_FIXTURES: dict[str, dict] = {
    "write-bug-report": {
        "outcome": "succeeded",
        "cost_usd": 0.05,
        "artifacts": {
            "bug-report.md": (
                "# Bug report\n\nparse_range returns half-open instead of inclusive.\n"
            ),
        },
    },
    "write-design-spec": {
        "outcome": "succeeded",
        "cost_usd": 0.10,
        "artifacts": {"design-spec.md": "# Design spec\n\nFix off-by-one in upper bound.\n"},
    },
    "review-design-spec-agent": {
        "outcome": "succeeded",
        "cost_usd": 0.04,
        "artifacts": {
            "design-spec-review-agent.json": json.dumps(
                {
                    "verdict": "approved",
                    "summary": "Design-spec is sound.",
                    "unresolved_concerns": [],
                    "addressed_in_this_iteration": [],
                }
            ),
        },
    },
    "write-impl-spec": {
        "outcome": "succeeded",
        "cost_usd": 0.20,
        "artifacts": {
            "impl-spec.md": "# Impl spec\n\nChange `range(a, b)` to `range(a, b + 1)`.\n",
        },
    },
    "review-impl-spec-agent": {
        "outcome": "succeeded",
        "cost_usd": 0.05,
        "artifacts": {
            "impl-spec-review-agent.json": json.dumps(
                {
                    "verdict": "approved",
                    "summary": "Impl-spec is precise.",
                    "unresolved_concerns": [],
                    "addressed_in_this_iteration": [],
                }
            ),
        },
    },
    "write-impl-plan-spec": {
        "outcome": "succeeded",
        "cost_usd": 0.15,
        "artifacts": {
            # Plan with no expanded stages — the FakeStageRunner does not
            # actually run an expander, so the plan is empty. The schema
            # validator only checks shape (`stages: list`), not contents.
            "plan.yaml": yaml.safe_dump({"stages": []}),
        },
    },
    "review-impl-plan-spec-agent": {
        "outcome": "succeeded",
        "cost_usd": 0.04,
        "artifacts": {
            "impl-plan-spec-review-agent.json": json.dumps(
                {
                    "verdict": "approved",
                    "summary": "Plan covers the change cleanly.",
                    "unresolved_concerns": [],
                    "addressed_in_this_iteration": [],
                }
            ),
        },
    },
    "run-integration-tests": {
        "outcome": "succeeded",
        "cost_usd": 0.30,
        "artifacts": {
            "integration-test-report.json": json.dumps(
                {
                    "verdict": "passed",
                    "summary": "All 3 tests pass after the fix.",
                    "test_command": "pytest tests/",
                    "total_count": 3,
                    "passed_count": 3,
                    "failed_count": 0,
                    "skipped_count": 0,
                    "failures": [],
                    "duration_seconds": 0.42,
                }
            ),
        },
    },
    "write-summary": {
        "outcome": "succeeded",
        "cost_usd": 0.06,
        "artifacts": {
            "summary.md": (
                "# Job summary\n\nFixed `parse_range` off-by-one. PR opened at "
                "https://example.invalid/pull/42 (mock URL — fake-fixtures run).\n"
            ),
        },
    },
}

# Human-gate stages, in dispatch order. Each pair is (stage_id, output_artifact_name).
# All four are review gates that produce a ``review-verdict-schema`` artifact.
# ``review-integration-tests-human`` has ``runs_if`` predicated on integration
# tests *not* passing; the fixture above sets verdict="passed" so that stage
# is skipped at dispatch and is intentionally absent from this list.
_HUMAN_GATES: list[tuple[str, str]] = [
    ("review-design-spec-human", "design-spec-review-human.json"),
    ("review-impl-spec-human", "impl-spec-review-human.json"),
    ("review-impl-plan-spec-human", "impl-plan-spec-review-human.json"),
]

_APPROVED_VERDICT: dict = {
    "verdict": "approved",
    "summary": "Approved by operator (e2e fake).",
    "unresolved_concerns": [],
    "addressed_in_this_iteration": [],
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_fake_fixtures(fixtures_dir: Path) -> None:
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    for stage_id, payload in _AGENT_FIXTURES.items():
        (fixtures_dir / f"{stage_id}.yaml").write_text(yaml.safe_dump(payload))


def _register_fake_project(root: Path, *, slug: str, repo_path: Path) -> ProjectConfig:
    """Write a project.json directly. Skips the CLI (which requires gh + a
    real remote). The compiler only reads project.json + repo_path."""
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / ".git").mkdir(exist_ok=True)
    (repo_path / "CLAUDE.md").write_text("# fake project for e2e\n")
    project = ProjectConfig(
        slug=slug,
        name=slug,
        repo_path=str(repo_path),
        remote_url=f"https://github.com/example/{slug}",
        default_branch="main",
        created_at=datetime.now(UTC),
    )
    atomic_write_json(paths.project_json(slug, root=root), project)
    overrides = paths.project_overrides_root(repo_path)
    (overrides / "job-template-overrides").mkdir(parents=True, exist_ok=True)
    return project


async def _wait_for_state(
    root: Path,
    job_slug: str,
    accept: Iterable[JobState],
    *,
    timeout: float = 20.0,
    poll: float = 0.1,
) -> JobState:
    """Poll job.json until its state is one of *accept* or timeout expires."""
    accept_set = set(accept)
    job_json = paths.job_json(job_slug, root=root)
    deadline = asyncio.get_event_loop().time() + timeout
    last_state: JobState | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            cfg = JobConfig.model_validate_json(job_json.read_text())
            last_state = cfg.state
            if last_state in accept_set:
                return last_state
        except (FileNotFoundError, ValueError):
            pass
        await asyncio.sleep(poll)
    raise AssertionError(
        f"job {job_slug!r} did not reach any of {sorted(s.value for s in accept_set)} "
        f"within {timeout}s (last seen: {last_state})"
    )


def _resolve_human_gate(
    root: Path,
    job_slug: str,
    stage_id: str,
    output_filename: str,
    verdict: dict = _APPROVED_VERDICT,
) -> None:
    """Write the human-stage's required output artifact and mark stage.json
    SUCCEEDED so the resumed driver skips the stage on the next pass.

    Models what the production HIL → form pipeline → artifact bridge will do
    when a human submits an answer; that bridge is deferred to v1+ but the
    on-disk shape it must produce is exactly this.
    """
    job_dir = paths.job_dir(job_slug, root=root)
    artifact_path = job_dir / output_filename
    artifact_path.write_text(json.dumps(verdict))

    stage_json = paths.stage_json(job_slug, stage_id, root=root)
    if stage_json.exists():
        existing = StageRun.model_validate_json(stage_json.read_text())
        attempt = existing.attempt
    else:
        attempt = 1
    resolved = StageRun(
        stage_id=stage_id,
        attempt=attempt,
        state=StageState.SUCCEEDED,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        cost_accrued=0.0,
    )
    atomic_write_json(stage_json, resolved)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_bug_full_lifecycle(tmp_path: Path) -> None:
    """Submit a fix-bug job, walk all 13 stages, resolve 3 human gates,
    and verify COMPLETED with summary.md."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    fixtures_dir = tmp_path / "fakes"
    _write_fake_fixtures(fixtures_dir)
    _register_fake_project(root, slug="lifecycle-target", repo_path=tmp_path / "repo")

    settings = Settings(root=root, fake_fixtures_dir=fixtures_dir)
    app = create_app(settings)

    with TestClient(app) as client:
        # Submit the job through the real HTTP endpoint; the dashboard
        # spawns the driver as a side effect.
        resp = client.post(
            "/api/jobs",
            json={
                "project_slug": "lifecycle-target",
                "job_type": "fix-bug",
                "title": "off-by-one in parse_range",
                "request_text": (
                    "parse_range('1-3') returns [1, 2] instead of [1, 2, 3]; "
                    "the docstring promises an inclusive range. Fix it."
                ),
            },
        )
        assert resp.status_code == 201, resp.text
        job_slug = resp.json()["job_slug"]

    # Walk through human gates one by one. Each iteration: the driver runs
    # to the next BLOCKED_ON_HUMAN, the test resolves that gate by writing
    # its output + flipping stage.json to SUCCEEDED, then re-spawns the
    # driver. After the last gate the driver should reach COMPLETED.
    terminal = {JobState.COMPLETED, JobState.FAILED, JobState.ABANDONED}
    block_or_terminal = {JobState.BLOCKED_ON_HUMAN, *terminal}

    for stage_id, output_filename in _HUMAN_GATES:
        state = await _wait_for_state(root, job_slug, block_or_terminal, timeout=30.0)
        assert state == JobState.BLOCKED_ON_HUMAN, f"expected to block on {stage_id!r}, got {state}"
        _resolve_human_gate(root, job_slug, stage_id, output_filename)
        await spawn_driver(job_slug, root=root, fake_fixtures_dir=fixtures_dir)

    final_state = await _wait_for_state(root, job_slug, terminal, timeout=30.0)
    assert final_state == JobState.COMPLETED, (
        f"job did not reach COMPLETED; final state = {final_state}"
    )

    job_dir = paths.job_dir(job_slug, root=root)
    summary = job_dir / "summary.md"
    assert summary.exists(), f"summary.md missing in {job_dir}"
    assert "PR" in summary.read_text(), "summary.md should reference the opened PR"

    # Sanity: every required final artifact landed on disk.
    for stage_outputs in (
        ("bug-report.md",),
        ("design-spec.md", "design-spec-review-agent.json", "design-spec-review-human.json"),
        ("impl-spec.md", "impl-spec-review-agent.json", "impl-spec-review-human.json"),
        (
            "plan.yaml",
            "impl-plan-spec-review-agent.json",
            "impl-plan-spec-review-human.json",
        ),
        ("integration-test-report.json",),
        ("summary.md",),
    ):
        for artifact in stage_outputs:
            assert (job_dir / artifact).exists(), f"missing artifact {artifact} in {job_dir}"
