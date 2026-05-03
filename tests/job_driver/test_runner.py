"""Tests for JobDriver state machine.

Covers:
- SUBMITTED → STAGES_RUNNING transition on start
- All stages succeed → COMPLETED
- One stage fails → FAILED
- Cancel via command file (human-action.json)
- Cancel via SIGTERM
- Resume: outputs already present → stage skipped
- loop_back: re-enters target stage when condition holds
- runs_if: stage skipped when predicate is false
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from datetime import UTC, datetime
from pathlib import Path

import yaml

from job_driver.runner import JobDriver
from job_driver.stage_runner import FakeStageRunner, StageResult
from shared import paths
from shared.models.job import JobConfig, JobState
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    LoopBack,
    OnExhaustion,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
    StageRun,
    StageState,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_stage(
    stage_id: str,
    required_outputs: list[str] | None = None,
    required_inputs: list[str] | None = None,
    worker: str = "agent",
    runs_if: str | None = None,
    loop_back: LoopBack | None = None,
) -> StageDefinition:
    ro = [RequiredOutput(path=p) for p in required_outputs] if required_outputs else None
    return StageDefinition(
        id=stage_id,
        worker=worker,  # type: ignore[arg-type]
        inputs=InputSpec(required=required_inputs or [], optional=None),
        outputs=OutputSpec(required=required_outputs or []),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(required_outputs=ro),
        runs_if=runs_if,
        loop_back=loop_back,
    )


def _write_stage_list(job_dir: Path, stages: list[StageDefinition]) -> None:
    stage_list_path = job_dir / "stage-list.yaml"
    data = {"stages": [json.loads(s.model_dump_json()) for s in stages]}
    stage_list_path.write_text(yaml.dump(data))


def _write_job_config(job_dir: Path, state: JobState = JobState.SUBMITTED) -> JobConfig:
    config = JobConfig(
        job_id="test-job-001",
        job_slug=job_dir.name,
        project_slug="test-project",
        job_type="build-feature",
        created_at=datetime.now(UTC),
        created_by="human",
        state=state,
    )
    from shared.atomic import atomic_write_json

    job_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(job_dir / "job.json", config)
    return config


def _read_job_config(job_dir: Path) -> JobConfig:
    return JobConfig.model_validate_json((job_dir / "job.json").read_text())


def _read_stage_run(job_dir: Path, stage_id: str) -> StageRun | None:
    p = job_dir / "stages" / stage_id / "stage.json"
    if not p.exists():
        return None
    return StageRun.model_validate_json(p.read_text())


def _make_driver(
    job_dir: Path,
    fixtures_dir: Path,
    *,
    root: Path | None = None,
    heartbeat_interval: float = 0.1,
    fixed_time: datetime | None = None,
) -> JobDriver:
    now_fn = (lambda: fixed_time) if fixed_time else None
    return JobDriver(
        job_dir.name,
        root=root or job_dir.parent,
        stage_runner=FakeStageRunner(fixtures_dir),
        heartbeat_interval=heartbeat_interval,
        now_fn=now_fn,  # type: ignore[arg-type]
    )


def _write_fixture(fixtures_dir: Path, stage_id: str, content: dict) -> None:
    (fixtures_dir / f"{stage_id}.yaml").write_text(yaml.dump(content))


# ---------------------------------------------------------------------------
# Tests: state machine transitions
# ---------------------------------------------------------------------------


async def test_submitted_transitions_to_stages_running(tmp_path: Path) -> None:
    """Job starts SUBMITTED; driver transitions to STAGES_RUNNING."""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-a", required_outputs=["out-a.txt"])])
    _write_fixture(
        fixtures_dir, "stage-a", {"outcome": "succeeded", "artifacts": {"out-a.txt": "done"}}
    )

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
    await driver.run()

    final = _read_job_config(job_dir)
    assert final.state == JobState.COMPLETED


async def test_all_stages_succeed_completes_job(tmp_path: Path) -> None:
    """All stages succeed → job reaches COMPLETED."""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    stages = [
        _make_stage("stage-a", required_outputs=["a.txt"]),
        _make_stage("stage-b", required_outputs=["b.txt"], required_inputs=["a.txt"]),
        _make_stage("stage-c", required_outputs=["c.txt"], required_inputs=["b.txt"]),
    ]
    _write_job_config(job_dir)
    _write_stage_list(job_dir, stages)
    for s, out in [("stage-a", "a.txt"), ("stage-b", "b.txt"), ("stage-c", "c.txt")]:
        _write_fixture(fixtures_dir, s, {"outcome": "succeeded", "artifacts": {out: f"{s} output"}})

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
    await driver.run()

    assert _read_job_config(job_dir).state == JobState.COMPLETED
    # Each stage transitioned to SUCCEEDED
    for stage_id in ["stage-a", "stage-b", "stage-c"]:
        sr = _read_stage_run(job_dir, stage_id)
        assert sr is not None
        assert sr.state == StageState.SUCCEEDED


async def test_stage_failure_fails_job(tmp_path: Path) -> None:
    """A failing stage transitions job to FAILED."""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-a", required_outputs=["out.txt"])])
    _write_fixture(fixtures_dir, "stage-a", {"outcome": "failed", "reason": "simulated failure"})

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
    await driver.run()

    assert _read_job_config(job_dir).state == JobState.FAILED
    sr = _read_stage_run(job_dir, "stage-a")
    assert sr is not None
    assert sr.state == StageState.FAILED


async def test_resume_skips_completed_stages(tmp_path: Path) -> None:
    """Re-running driver with outputs already present skips completed stages."""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    stages = [
        _make_stage("stage-a", required_outputs=["a.txt"]),
        _make_stage("stage-b", required_outputs=["b.txt"]),
    ]
    _write_job_config(job_dir, state=JobState.STAGES_RUNNING)
    _write_stage_list(job_dir, stages)

    # stage-a already completed: output exists on disk
    (job_dir / "a.txt").write_text("already done")
    # stage-b needs to run
    _write_fixture(
        fixtures_dir, "stage-b", {"outcome": "succeeded", "artifacts": {"b.txt": "b out"}}
    )

    # Track which stages actually ran
    ran_stages: list[str] = []
    original_run = FakeStageRunner.run

    async def _tracking_run(self, stage_def, job_dir, stage_run_dir):
        ran_stages.append(stage_def.id)
        return await original_run(self, stage_def, job_dir, stage_run_dir)

    FakeStageRunner.run = _tracking_run  # type: ignore[method-assign]
    try:
        driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
        await driver.run()
    finally:
        FakeStageRunner.run = original_run  # type: ignore[method-assign]

    assert "stage-a" not in ran_stages, "stage-a should have been skipped (already succeeded)"
    assert "stage-b" in ran_stages
    assert _read_job_config(job_dir).state == JobState.COMPLETED


async def test_runs_if_false_skips_stage(tmp_path: Path) -> None:
    """Stage with runs_if=false is skipped; job still completes."""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    stages = [
        _make_stage("stage-a", required_outputs=["a.txt"]),
        # runs_if = false literal → always skip
        _make_stage("stage-skip", required_outputs=["skip.txt"], runs_if="false"),
        _make_stage("stage-b", required_outputs=["b.txt"]),
    ]
    _write_job_config(job_dir)
    _write_stage_list(job_dir, stages)
    _write_fixture(fixtures_dir, "stage-a", {"outcome": "succeeded", "artifacts": {"a.txt": "a"}})
    _write_fixture(fixtures_dir, "stage-b", {"outcome": "succeeded", "artifacts": {"b.txt": "b"}})

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
    await driver.run()

    assert _read_job_config(job_dir).state == JobState.COMPLETED
    # skip stage should not have a stage.json
    assert not (job_dir / "stages" / "stage-skip" / "stage.json").exists()


async def test_loop_back_reruns_target(tmp_path: Path) -> None:
    """loop_back: reviewer → writer loop when verdict != approved."""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    import json

    stages = [
        _make_stage("write-spec", required_outputs=["spec.md"]),
        _make_stage(
            "review-spec",
            required_outputs=["spec-review.json"],
            required_inputs=["spec.md"],
            loop_back=LoopBack(
                to="write-spec",
                condition="spec-review.json.verdict != 'approved'",
                max_iterations=2,
                on_exhaustion=OnExhaustion(
                    kind="hil-manual-step",
                    prompt="Loop exhausted.",
                ),
            ),
        ),
    ]
    _write_job_config(job_dir)
    _write_stage_list(job_dir, stages)

    # First review → rejected; second review → approved
    call_count: dict[str, int] = {"write-spec": 0, "review-spec": 0}
    original_run = FakeStageRunner.run

    async def _counted_run(self, stage_def, job_dir_arg, stage_run_dir):
        call_count[stage_def.id] = call_count.get(stage_def.id, 0) + 1
        if stage_def.id == "write-spec":
            (job_dir_arg / "spec.md").write_text(f"spec v{call_count['write-spec']}")
            return StageResult(succeeded=True, outputs_produced=["spec.md"])
        elif stage_def.id == "review-spec":
            run_n = call_count["review-spec"]
            verdict = "rejected" if run_n == 1 else "approved"
            payload = json.dumps({"verdict": verdict})
            (job_dir_arg / "spec-review.json").write_text(payload)
            return StageResult(succeeded=True, outputs_produced=["spec-review.json"])
        return await original_run(self, stage_def, job_dir_arg, stage_run_dir)

    FakeStageRunner.run = _counted_run  # type: ignore[method-assign]
    try:
        driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
        await driver.run()
    finally:
        FakeStageRunner.run = original_run  # type: ignore[method-assign]

    assert call_count["write-spec"] == 2, "write-spec should run twice (original + 1 loop)"
    assert call_count["review-spec"] == 2
    assert _read_job_config(job_dir).state == JobState.COMPLETED


async def test_cancel_via_command_file(tmp_path: Path) -> None:
    """Writing human-action.json with cancel triggers ABANDONED."""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    # Use a long delay so the driver is running when we write the cancel file
    _write_fixture(
        fixtures_dir,
        "stage-long",
        {"outcome": "succeeded", "delay_seconds": 10.0, "artifacts": {"out.txt": "x"}},
    )
    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-long", required_outputs=["out.txt"])])

    async def _cancel_after_short_delay() -> None:
        await asyncio.sleep(0.1)
        cancel_payload = json.dumps({"command": "cancel", "reason": "human"})
        (job_dir / "human-action.json").write_text(cancel_payload)

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
    await asyncio.gather(
        driver.run(),
        _cancel_after_short_delay(),
    )

    final = _read_job_config(job_dir)
    assert final.state == JobState.ABANDONED


async def test_cancel_via_sigterm(tmp_path: Path) -> None:
    """SIGTERM sent to the current process transitions job to ABANDONED."""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_fixture(
        fixtures_dir,
        "stage-long",
        {"outcome": "succeeded", "delay_seconds": 10.0, "artifacts": {"out.txt": "x"}},
    )
    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-long", required_outputs=["out.txt"])])

    async def _send_sigterm_after_delay() -> None:
        await asyncio.sleep(0.1)
        os.kill(os.getpid(), signal.SIGTERM)

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
    await asyncio.gather(
        driver.run(),
        _send_sigterm_after_delay(),
    )

    final = _read_job_config(job_dir)
    assert final.state == JobState.ABANDONED


async def test_heartbeat_written(tmp_path: Path) -> None:
    """Heartbeat file is created during the run."""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-a", required_outputs=["a.txt"])])
    _write_fixture(fixtures_dir, "stage-a", {"outcome": "succeeded", "artifacts": {"a.txt": "x"}})

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
    await driver.run()

    hb_path = paths.job_heartbeat(job_dir.name, root=tmp_path)
    assert hb_path.exists(), "heartbeat file should be created"


async def test_events_appended_to_jsonl(tmp_path: Path) -> None:
    """job_state_transition events are appended to events.jsonl."""
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-a", required_outputs=["a.txt"])])
    _write_fixture(fixtures_dir, "stage-a", {"outcome": "succeeded", "artifacts": {"a.txt": "x"}})

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
    await driver.run()

    events_path = paths.job_events_jsonl(job_dir.name, root=tmp_path)
    assert events_path.exists()
    events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    assert len(events) >= 2, "expect at least SUBMITTED→STAGES_RUNNING and STAGES_RUNNING→COMPLETED"
    event_types = [e["event_type"] for e in events]
    assert "job_state_transition" in event_types
