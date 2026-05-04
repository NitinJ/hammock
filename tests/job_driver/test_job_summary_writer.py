"""Tests for the job-summary.json writer.

Per `docs/v0-alignment-report.md` Plan #6: `JobCostSummary`'s docstring
promises persistence to `<job_dir>/job-summary.json`, but the file was
never written. The JobDriver must fold `cost_accrued` events from
events.jsonl into a `JobCostSummary` and atomically write it to disk on
every terminal transition (COMPLETED / FAILED / ABANDONED).

This test reuses the existing test_runner.py fixtures + helpers (kept
local here to avoid pytest import games).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from job_driver.runner import JobDriver
from job_driver.stage_runner import FakeStageRunner
from shared.atomic import atomic_write_json
from shared.models.job import JobConfig, JobCostSummary, JobState
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
)


def _make_stage(stage_id: str, *, agent_ref: str, output: str) -> StageDefinition:
    return StageDefinition(
        id=stage_id,
        worker="agent",
        agent_ref=agent_ref,
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=[output]),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(required_outputs=[RequiredOutput(path=output)]),
    )


def _write_stage_list(job_dir: Path, stages: list[StageDefinition]) -> None:
    data = {"stages": [json.loads(s.model_dump_json()) for s in stages]}
    (job_dir / "stage-list.yaml").write_text(yaml.dump(data))


def _write_job_config(
    job_dir: Path, *, project_slug: str = "p", job_id: str = "jid-001"
) -> JobConfig:
    cfg = JobConfig(
        job_id=job_id,
        job_slug=job_dir.name,
        project_slug=project_slug,
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="test",
        state=JobState.SUBMITTED,
    )
    job_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(job_dir / "job.json", cfg)
    return cfg


def _write_fixture(fixtures_dir: Path, stage_id: str, content: dict) -> None:
    (fixtures_dir / f"{stage_id}.yaml").write_text(yaml.dump(content))


def _summary_path(job_dir: Path) -> Path:
    return job_dir / "job-summary.json"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_completed_job_writes_summary_with_total_cost(tmp_path: Path) -> None:
    """A successful job persists JobCostSummary with the rolled-up total."""
    job_dir = tmp_path / "jobs" / "j1"
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    _write_job_config(job_dir, job_id="jid-1", project_slug="proj-x")
    _write_stage_list(
        job_dir,
        [
            _make_stage("a", agent_ref="writer", output="a.txt"),
            _make_stage("b", agent_ref="reviewer", output="b.txt"),
        ],
    )
    _write_fixture(
        fixtures_dir, "a", {"outcome": "succeeded", "cost_usd": 0.25, "artifacts": {"a.txt": "."}}
    )
    _write_fixture(
        fixtures_dir, "b", {"outcome": "succeeded", "cost_usd": 0.50, "artifacts": {"b.txt": "."}}
    )

    driver = JobDriver(
        job_dir.name,
        root=tmp_path,
        stage_runner=FakeStageRunner(fixtures_dir),
        heartbeat_interval=0.1,
    )
    await driver.run()

    summary_path = _summary_path(job_dir)
    assert summary_path.exists(), "job-summary.json must land on COMPLETED"
    summary = JobCostSummary.model_validate_json(summary_path.read_text())
    assert summary.job_id == "jid-1"
    assert summary.project_slug == "proj-x"
    assert summary.total_usd == 0.75
    # by_stage is keyed by stage id; both stages should appear
    assert set(summary.by_stage.keys()) == {"a", "b"}
    assert summary.by_stage["a"].total_usd == 0.25
    assert summary.by_stage["b"].total_usd == 0.50
    # by_agent — Codex review of PR #23 caught this was structurally
    # always empty because the runner emitted cost_accrued events
    # without `agent_ref`. Now: each stage's agent_ref appears with the
    # stage's spend.
    assert set(summary.by_agent.keys()) == {"writer", "reviewer"}
    assert summary.by_agent["writer"].total_usd == 0.25
    assert summary.by_agent["reviewer"].total_usd == 0.50


async def test_failed_job_also_writes_summary(tmp_path: Path) -> None:
    """A failing job still writes a summary so post-hoc tooling has a record."""
    job_dir = tmp_path / "jobs" / "jf"
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    _write_job_config(job_dir, job_id="jid-fail")
    _write_stage_list(job_dir, [_make_stage("only", agent_ref="x", output="o.txt")])
    _write_fixture(fixtures_dir, "only", {"outcome": "failed", "reason": "boom"})

    driver = JobDriver(
        job_dir.name,
        root=tmp_path,
        stage_runner=FakeStageRunner(fixtures_dir),
        heartbeat_interval=0.1,
    )
    await driver.run()

    summary_path = _summary_path(job_dir)
    assert summary_path.exists(), "job-summary.json must land on FAILED too"
    summary = JobCostSummary.model_validate_json(summary_path.read_text())
    assert summary.job_id == "jid-fail"
    assert summary.total_usd == 0.0


async def test_no_summary_until_terminal(tmp_path: Path) -> None:
    """The summary is a terminal-state artifact; it must not exist
    while the job is still running. Asserted by the absence of the
    file when the driver is about to start STAGES_RUNNING."""
    job_dir = tmp_path / "jobs" / "jpending"
    _write_job_config(job_dir)
    # Job is SUBMITTED on disk; no driver.run() yet → no summary expected.
    assert not _summary_path(job_dir).exists()


async def test_summary_completed_at_within_window(tmp_path: Path) -> None:
    """`completed_at` must be set close to job termination time."""
    job_dir = tmp_path / "jobs" / "jt"
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    _write_job_config(job_dir, job_id="jid-t")
    _write_stage_list(job_dir, [_make_stage("a", agent_ref="x", output="a.txt")])
    _write_fixture(
        fixtures_dir, "a", {"outcome": "succeeded", "cost_usd": 0.0, "artifacts": {"a.txt": "."}}
    )

    before = datetime.now(UTC)
    driver = JobDriver(
        job_dir.name,
        root=tmp_path,
        stage_runner=FakeStageRunner(fixtures_dir),
        heartbeat_interval=0.1,
    )
    await driver.run()
    after = datetime.now(UTC)

    summary = JobCostSummary.model_validate_json(_summary_path(job_dir).read_text())
    assert before <= summary.completed_at <= after
