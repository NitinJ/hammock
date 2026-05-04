"""Tests for dynamic stage expansion (P3 — real-claude e2e precondition track).

The driver originally read ``stage-list.yaml`` once at the top of
``_execute_stages`` and iterated over the resulting list. Production
templates use an expander stage (``is_expander: true``) that appends
the actual implementation stages at runtime; without re-reading,
those appended stages are never executed and real-mode runs of either
template terminate prematurely.

Contract:

- After any stage with ``is_expander=True`` succeeds, the driver
  re-reads ``stage-list.yaml`` before stepping to the next stage.
- Non-expander stages cannot inject new stages — only
  ``is_expander=True`` triggers a re-read.
- Expansion is bounded to a safety cap (1000 stages per job) to
  catch runaway expanders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from job_driver.runner import JobDriver
from job_driver.stage_runner import StageResult
from shared.models.job import JobState
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    StageDefinition,
)
from tests.job_driver.test_runner import (
    _make_stage,
    _read_job_config,
    _write_job_config,
    _write_stage_list,
)


def _expander_stage(stage_id: str) -> StageDefinition:
    return StageDefinition(
        id=stage_id,
        worker="agent",
        inputs=InputSpec(),
        outputs=OutputSpec(required=[]),
        budget=Budget(max_turns=1),
        exit_condition=ExitCondition(),
        is_expander=True,
    )


class _ExpandingRunner:
    """Runner that, when ``trigger_id`` runs, appends new stages to
    ``stage-list.yaml`` (mimicking a real expander stage's effect)."""

    def __init__(
        self,
        *,
        job_dir: Path,
        trigger_id: str,
        appended: list[StageDefinition],
    ) -> None:
        self._job_dir = job_dir
        self._trigger_id = trigger_id
        self._appended = appended
        self.calls: list[str] = []

    async def run(
        self,
        stage_def: StageDefinition,
        job_dir: Path,
        stage_run_dir: Path,
    ) -> StageResult:
        del stage_run_dir
        self.calls.append(stage_def.id)
        if stage_def.id == self._trigger_id:
            # Append the new stages to stage-list.yaml.
            stage_list_path = job_dir / "stage-list.yaml"
            data: dict[str, Any] = yaml.safe_load(stage_list_path.read_text())
            for s in self._appended:
                data["stages"].append(s.model_dump(mode="json"))
            stage_list_path.write_text(yaml.dump(data))
        return StageResult(succeeded=True)


@pytest.mark.asyncio
async def test_appended_stages_run_after_expander_succeeds(tmp_path: Path) -> None:
    job_dir = tmp_path / "jobs" / "exp-job"
    job_dir.mkdir(parents=True)
    _write_job_config(job_dir)

    initial = [
        _make_stage("a"),
        _expander_stage("b-expander"),
    ]
    appended = [_make_stage("c"), _make_stage("d")]
    _write_stage_list(job_dir, initial)

    runner = _ExpandingRunner(job_dir=job_dir, trigger_id="b-expander", appended=appended)
    driver = JobDriver(job_dir.name, root=tmp_path, stage_runner=runner)
    await driver.run()

    assert runner.calls == ["a", "b-expander", "c", "d"]
    assert _read_job_config(job_dir).state == JobState.COMPLETED


@pytest.mark.asyncio
async def test_non_expander_stage_appending_is_not_picked_up(tmp_path: Path) -> None:
    """A non-expander stage that mutates stage-list.yaml does NOT trigger
    a re-read. The appended stages are inert."""
    job_dir = tmp_path / "jobs" / "noexp-job"
    job_dir.mkdir(parents=True)
    _write_job_config(job_dir)

    initial = [_make_stage("a"), _make_stage("b")]
    appended = [_make_stage("c")]
    _write_stage_list(job_dir, initial)

    # ``a`` mutates the YAML but is not flagged is_expander.
    runner = _ExpandingRunner(job_dir=job_dir, trigger_id="a", appended=appended)
    driver = JobDriver(job_dir.name, root=tmp_path, stage_runner=runner)
    await driver.run()

    # ``c`` was written to disk but never executed.
    assert runner.calls == ["a", "b"]


@pytest.mark.asyncio
async def test_expander_failure_does_not_trigger_reread(tmp_path: Path) -> None:
    """Re-read fires only on SUCCEEDED + is_expander; a failing expander
    must not re-read (it would mask the failure)."""

    class _FailingExpander:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def run(
            self,
            stage_def: StageDefinition,
            job_dir: Path,
            stage_run_dir: Path,
        ) -> StageResult:
            del job_dir, stage_run_dir
            self.calls.append(stage_def.id)
            if stage_def.id == "expander":
                return StageResult(succeeded=False, reason="boom")
            return StageResult(succeeded=True)

    job_dir = tmp_path / "jobs" / "fail-job"
    job_dir.mkdir(parents=True)
    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("first"), _expander_stage("expander")])

    runner = _FailingExpander()
    driver = JobDriver(job_dir.name, root=tmp_path, stage_runner=runner)
    await driver.run()

    assert runner.calls == ["first", "expander"]
    assert _read_job_config(job_dir).state == JobState.FAILED
