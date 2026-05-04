"""Tests for v0 budget enforcement.

Per `docs/v0-alignment-report.md` Plan #1: design summary calls bounded
budgets *mandatory* — *"Every worker run has a hard cap. Budget
overruns are first-class errors. Workers cannot disable their own
budgets."* Today the runner consults none of the caps; this test
suite asserts the enforcement contract:

- ``max_wall_clock_min`` — JobDriver kills the stage attempt when the
  cap elapses. Works for any runner.
- ``max_budget_usd`` — JobDriver fails the stage when the runner
  reports cost above the cap (post-check). RealStageRunner additionally
  passes ``--max-budget-usd`` to claude so claude self-aborts before
  overshoot when possible.
- ``max_turns`` — not enforced in v0 (claude CLI has no ``--max-turns``
  flag and stream-counting is deferred); see the model docstring +
  ``implementation.md § 9``.

Wall-clock cap is the hardest to test cheaply. Budget model accepts a
fractional minute value (``float | None``) so tests can assert with
e.g. 0.02 minutes (1.2 s).
"""

from __future__ import annotations

import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import yaml

from job_driver.runner import JobDriver
from job_driver.stage_runner import FakeStageRunner, RealStageRunner
from shared.atomic import atomic_write_json
from shared.models.job import JobConfig, JobState
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
    StageState,
)


def _stage(stage_id: str, *, output: str, budget: Budget) -> StageDefinition:
    return StageDefinition(
        id=stage_id,
        worker="agent",
        agent_ref="x",
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=[output]),
        budget=budget,
        exit_condition=ExitCondition(required_outputs=[RequiredOutput(path=output)]),
    )


def _seed_job(tmp_path: Path, stages: list[StageDefinition], *, slug: str = "j") -> Path:
    job_dir = tmp_path / "jobs" / slug
    job_dir.mkdir(parents=True)
    cfg = JobConfig(
        job_id="jid",
        job_slug=slug,
        project_slug="p",
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="test",
        state=JobState.SUBMITTED,
    )
    atomic_write_json(job_dir / "job.json", cfg)
    (job_dir / "stage-list.yaml").write_text(
        yaml.dump({"stages": [json.loads(s.model_dump_json()) for s in stages]})
    )
    return job_dir


def _read_stage(job_dir: Path, stage_id: str):
    from shared.models.stage import StageRun

    return StageRun.model_validate_json((job_dir / "stages" / stage_id / "stage.json").read_text())


# ---------------------------------------------------------------------------
# Wall-clock cap
# ---------------------------------------------------------------------------


async def test_wall_clock_overrun_fails_stage(tmp_path: Path) -> None:
    """A stage that sleeps past its wall-clock cap is killed and FAILED."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "slow.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "delay_seconds": 5.0, "artifacts": {"o.txt": "."}})
    )
    job_dir = _seed_job(
        tmp_path,
        # 0.02 min = 1.2 s; sleep is 5 s → overrun.
        [_stage("slow", output="o.txt", budget=Budget(max_wall_clock_min=0.02))],
    )

    driver = JobDriver(
        "j", root=tmp_path, stage_runner=FakeStageRunner(fixtures), heartbeat_interval=0.1
    )
    await driver.run()

    sr = _read_stage(job_dir, "slow")
    assert sr.state == StageState.FAILED
    final = JobConfig.model_validate_json((job_dir / "job.json").read_text())
    assert final.state == JobState.FAILED


async def test_under_wall_clock_cap_succeeds(tmp_path: Path) -> None:
    """A stage well within its cap completes normally."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "fast.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "delay_seconds": 0.05, "artifacts": {"o.txt": "."}})
    )
    job_dir = _seed_job(
        tmp_path,
        [_stage("fast", output="o.txt", budget=Budget(max_wall_clock_min=1.0))],
    )

    driver = JobDriver(
        "j", root=tmp_path, stage_runner=FakeStageRunner(fixtures), heartbeat_interval=0.1
    )
    await driver.run()

    sr = _read_stage(job_dir, "fast")
    assert sr.state == StageState.SUCCEEDED


# ---------------------------------------------------------------------------
# Budget USD cap
# ---------------------------------------------------------------------------


async def test_cost_overrun_fails_stage(tmp_path: Path) -> None:
    """Runner reports cost > cap → JobDriver overrides to FAILED.

    Even if the runner declares `succeeded=True`, an over-budget run
    must be a first-class error per design — workers cannot disable
    their own budgets, including by lying about cost.

    Codex review LOW 5: also assert that the stage.json after the
    override preserves the *actual* cost spent (so the cost rollup
    stays truthful) and the outputs the runner produced (so any
    forensic reading of the artifact set is accurate).
    """
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "spendy.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "cost_usd": 5.0, "artifacts": {"o.txt": "."}})
    )
    job_dir = _seed_job(
        tmp_path,
        [_stage("spendy", output="o.txt", budget=Budget(max_budget_usd=1.0))],
    )

    driver = JobDriver(
        "j", root=tmp_path, stage_runner=FakeStageRunner(fixtures), heartbeat_interval=0.1
    )
    await driver.run()

    sr = _read_stage(job_dir, "spendy")
    assert sr.state == StageState.FAILED
    # Real spend persisted, not zeroed — the rollup must be truthful.
    assert sr.cost_accrued == 5.0
    # Outputs the runner produced are recorded — forensic value.
    assert sr.outputs_produced == ["o.txt"]


async def test_under_cost_cap_succeeds(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "cheap.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "cost_usd": 0.10, "artifacts": {"o.txt": "."}})
    )
    job_dir = _seed_job(
        tmp_path,
        [_stage("cheap", output="o.txt", budget=Budget(max_budget_usd=1.0))],
    )

    driver = JobDriver(
        "j", root=tmp_path, stage_runner=FakeStageRunner(fixtures), heartbeat_interval=0.1
    )
    await driver.run()

    sr = _read_stage(job_dir, "cheap")
    assert sr.state == StageState.SUCCEEDED


# ---------------------------------------------------------------------------
# claude --max-budget-usd plumbing (RealStageRunner)
# ---------------------------------------------------------------------------


FIXTURES = Path(__file__).parent.parent / "fixtures" / "recorded-streams"


async def test_real_runner_passes_max_budget_usd_to_claude(tmp_path: Path) -> None:
    """When the stage's Budget.max_budget_usd is set, RealStageRunner
    appends `--max-budget-usd <n>` to the claude argv. The flag is
    documented in `claude --help` and is the cheapest enforcement
    layer — claude self-aborts if it would otherwise exceed."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run"
    stage_run_dir.mkdir()

    args_dump = tmp_path / "argv.txt"
    fixture_path = FIXTURES / "simple_success.jsonl"
    fake_claude = tmp_path / "fake_argv_claude"
    fake_claude.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > {args_dump}\ncat {fixture_path}\n'
    )
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(
        _stage("s", output="o.txt", budget=Budget(max_budget_usd=2.5)),
        tmp_path,
        stage_run_dir,
    )

    args = args_dump.read_text().splitlines()
    assert "--max-budget-usd" in args, f"--max-budget-usd missing: {args}"
    # Following positional arg should be the cap value
    idx = args.index("--max-budget-usd")
    assert args[idx + 1] == "2.5"


async def test_wall_clock_wins_tie_with_stage_completion(tmp_path: Path) -> None:
    """Codex review LOW 4 — when the stage and wall-clock cap complete
    simultaneously (`delay_seconds == max_wall_clock_min * 60`), the
    wall-clock branch is checked first and wins. Documented policy:
    fail-closed on the cap rather than admit a marginal overrun.

    This is hard to make perfectly deterministic without a clock
    injector, but in practice the stage and watchdog tasks both
    resolve in the same event-loop tick when delays match, and
    asyncio.wait(FIRST_COMPLETED) returns both in `done`; the
    branch order in `_run_single_stage` then breaks the tie.
    """
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    # delay == cap (in seconds): both tasks finish in the same tick.
    cap_min = 0.02  # 1.2 s
    cap_seconds = cap_min * 60.0
    (fixtures / "edge.yaml").write_text(
        yaml.dump(
            {"outcome": "succeeded", "delay_seconds": cap_seconds, "artifacts": {"o.txt": "."}}
        )
    )
    job_dir = _seed_job(
        tmp_path,
        [_stage("edge", output="o.txt", budget=Budget(max_wall_clock_min=cap_min))],
    )

    driver = JobDriver(
        "j", root=tmp_path, stage_runner=FakeStageRunner(fixtures), heartbeat_interval=0.1
    )
    await driver.run()

    sr = _read_stage(job_dir, "edge")
    # Wall-clock branch ran first — stage marked FAILED with the
    # documented cap-name reason rather than SUCCEEDED.
    assert sr.state == StageState.FAILED


async def test_real_runner_omits_max_budget_when_unset(tmp_path: Path) -> None:
    """Stage with only max_wall_clock_min → no --max-budget-usd flag."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run"
    stage_run_dir.mkdir()

    args_dump = tmp_path / "argv.txt"
    fixture_path = FIXTURES / "simple_success.jsonl"
    fake_claude = tmp_path / "fake_argv_claude"
    fake_claude.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > {args_dump}\ncat {fixture_path}\n'
    )
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(
        _stage("s", output="o.txt", budget=Budget(max_wall_clock_min=10)),
        tmp_path,
        stage_run_dir,
    )
    args = args_dump.read_text().splitlines()
    assert "--max-budget-usd" not in args
