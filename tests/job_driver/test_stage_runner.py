"""Tests for FakeStageRunner.

All tests run against stubs first (NotImplementedError), then pass after impl.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from job_driver.stage_runner import FakeStageRunner
from shared.models.stage import Budget, ExitCondition, InputSpec, OutputSpec, StageDefinition

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stage(
    stage_id: str = "write-problem-spec",
    required_outputs: list[str] | None = None,
    worker: str = "agent",
) -> StageDefinition:
    outputs = required_outputs or []
    return StageDefinition(
        id=stage_id,
        worker=worker,  # type: ignore[arg-type]
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=outputs),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(required_outputs=None),
    )


def _write_fixture(fixtures_dir: Path, stage_id: str, content: dict) -> None:
    (fixtures_dir / f"{stage_id}.yaml").write_text(yaml.dump(content))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_fake_runner_no_fixture_succeeds(tmp_path: Path) -> None:
    """No fixture → stage succeeds with no outputs (safe default)."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    stage_run_dir = tmp_path / "run-1"
    stage_run_dir.mkdir()

    runner = FakeStageRunner(fixtures_dir)
    result = await runner.run(
        _make_stage("write-problem-spec"),
        job_dir=tmp_path,
        stage_run_dir=stage_run_dir,
    )

    assert result.succeeded is True
    assert result.outputs_produced == []
    assert result.cost_usd == 0.0


async def test_fake_runner_writes_artifacts(tmp_path: Path) -> None:
    """Fixture with artifacts → files written to job_dir."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    stage_run_dir = tmp_path / "run-1"
    stage_run_dir.mkdir()

    _write_fixture(
        fixtures_dir,
        "write-problem-spec",
        {
            "outcome": "succeeded",
            "cost_usd": 0.05,
            "artifacts": {
                "problem-spec.md": "# Problem Spec\nTest content.",
            },
        },
    )

    runner = FakeStageRunner(fixtures_dir)
    result = await runner.run(
        _make_stage("write-problem-spec", required_outputs=["problem-spec.md"]),
        job_dir=tmp_path,
        stage_run_dir=stage_run_dir,
    )

    assert result.succeeded is True
    assert "problem-spec.md" in result.outputs_produced
    assert result.cost_usd == pytest.approx(0.05)
    assert (tmp_path / "problem-spec.md").exists()
    assert "Problem Spec" in (tmp_path / "problem-spec.md").read_text()


async def test_fake_runner_failure_outcome(tmp_path: Path) -> None:
    """Fixture outcome=failed → StageResult.succeeded is False."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    stage_run_dir = tmp_path / "run-1"
    stage_run_dir.mkdir()

    _write_fixture(
        fixtures_dir,
        "write-design-spec",
        {
            "outcome": "failed",
            "reason": "Simulated failure for test.",
        },
    )

    runner = FakeStageRunner(fixtures_dir)
    result = await runner.run(
        _make_stage("write-design-spec"),
        job_dir=tmp_path,
        stage_run_dir=stage_run_dir,
    )

    assert result.succeeded is False
    assert result.reason == "Simulated failure for test."


async def test_fake_runner_delay(tmp_path: Path) -> None:
    """Fixture delay_seconds is honoured (short value for test speed)."""
    import time

    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    stage_run_dir = tmp_path / "run-1"
    stage_run_dir.mkdir()

    _write_fixture(
        fixtures_dir,
        "write-problem-spec",
        {"outcome": "succeeded", "delay_seconds": 0.05},
    )

    runner = FakeStageRunner(fixtures_dir)
    t0 = time.monotonic()
    await runner.run(
        _make_stage("write-problem-spec"),
        job_dir=tmp_path,
        stage_run_dir=stage_run_dir,
    )
    elapsed = time.monotonic() - t0

    assert elapsed >= 0.04  # at least the sleep duration (with some tolerance)


async def test_fake_runner_verdict_artifact(tmp_path: Path) -> None:
    """Fixture can write a JSON verdict artifact (for loop_back tests)."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    stage_run_dir = tmp_path / "run-1"
    stage_run_dir.mkdir()

    import json

    verdict = {"verdict": "approved", "concerns": []}
    _write_fixture(
        fixtures_dir,
        "review-design-spec-agent",
        {
            "outcome": "succeeded",
            "artifacts": {
                "design-spec-review-agent.json": json.dumps(verdict),
            },
        },
    )

    runner = FakeStageRunner(fixtures_dir)
    result = await runner.run(
        _make_stage("review-design-spec-agent", required_outputs=["design-spec-review-agent.json"]),
        job_dir=tmp_path,
        stage_run_dir=stage_run_dir,
    )

    assert result.succeeded is True
    assert (tmp_path / "design-spec-review-agent.json").exists()
    data = json.loads((tmp_path / "design-spec-review-agent.json").read_text())
    assert data["verdict"] == "approved"
