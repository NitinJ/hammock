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
    ArtifactValidator,
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
    """Re-running driver with stage.json SUCCEEDED + outputs present skips completed stages.

    Per design § Recovery — resume requires BOTH stage.json reporting
    SUCCEEDED AND required outputs on disk. Stray output files alone must
    not be treated as completion (codex-review: prevents skipping a crashed
    stage that wrote partial outputs).
    """
    from shared.atomic import atomic_write_json

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

    # stage-a already completed: BOTH output AND stage.json SUCCEEDED.
    (job_dir / "a.txt").write_text("already done")
    sa_dir = job_dir / "stages" / "stage-a"
    sa_dir.mkdir(parents=True)
    atomic_write_json(
        sa_dir / "stage.json",
        StageRun(
            stage_id="stage-a",
            attempt=1,
            state=StageState.SUCCEEDED,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            outputs_produced=["a.txt"],
        ),
    )
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


async def test_resume_does_not_skip_stage_with_outputs_but_no_stage_json(tmp_path: Path) -> None:
    """Crash recovery: stray output files alone must not skip a stage.

    Spec: design § Recovery — must not treat partial/orphaned outputs as
    completion. Codex-review finding (Important).
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir, state=JobState.STAGES_RUNNING)
    _write_stage_list(job_dir, [_make_stage("stage-a", required_outputs=["a.txt"])])

    # Output exists but stage.json does NOT — this is the crashed-stage case.
    (job_dir / "a.txt").write_text("partial output from crashed prior run")
    _write_fixture(
        fixtures_dir, "stage-a", {"outcome": "succeeded", "artifacts": {"a.txt": "fresh output"}}
    )

    ran: list[str] = []
    original_run = FakeStageRunner.run

    async def _tracking_run(self, stage_def, job_dir_arg, stage_run_dir):
        ran.append(stage_def.id)
        return await original_run(self, stage_def, job_dir_arg, stage_run_dir)

    FakeStageRunner.run = _tracking_run  # type: ignore[method-assign]
    try:
        driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
        await driver.run()
    finally:
        FakeStageRunner.run = original_run  # type: ignore[method-assign]

    assert ran == ["stage-a"], "stage-a must re-run since its stage.json is absent"
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


# ---------------------------------------------------------------------------
# Tests added in response to codex review
# ---------------------------------------------------------------------------


async def test_human_stage_transitions_to_blocked_on_human(tmp_path: Path) -> None:
    """`worker: human` stages must NOT be auto-completed by the runner.

    Codex-review (Critical): the design requires
    STAGES_RUNNING → BLOCKED_ON_HUMAN for HIL waits.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    stages = [
        _make_stage("approve-spec", required_outputs=["approval.json"], worker="human"),
    ]
    _write_job_config(job_dir)
    _write_stage_list(job_dir, stages)

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
    await driver.run()

    cfg = _read_job_config(job_dir)
    assert cfg.state == JobState.BLOCKED_ON_HUMAN
    sr = _read_stage_run(job_dir, "approve-spec")
    assert sr is not None
    assert sr.state == StageState.BLOCKED_ON_HUMAN


async def test_human_stage_resumes_after_artifact_appears(tmp_path: Path) -> None:
    """After the human action lands on disk, a re-spawned driver advances."""
    from shared.atomic import atomic_write_json

    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    stages = [
        _make_stage("approve-spec", required_outputs=["approval.json"], worker="human"),
        _make_stage(
            "downstream",
            required_outputs=["next.txt"],
            required_inputs=["approval.json"],
        ),
    ]
    _write_job_config(job_dir)
    _write_stage_list(job_dir, stages)
    _write_fixture(
        fixtures_dir, "downstream", {"outcome": "succeeded", "artifacts": {"next.txt": "ok"}}
    )

    # First pass: blocks
    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
    await driver.run()
    assert _read_job_config(job_dir).state == JobState.BLOCKED_ON_HUMAN

    # Human action arrives: write the approval artifact + mark stage SUCCEEDED
    (job_dir / "approval.json").write_text('{"approved": true}')
    sa_dir = job_dir / "stages" / "approve-spec"
    sa_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        sa_dir / "stage.json",
        StageRun(
            stage_id="approve-spec",
            attempt=1,
            state=StageState.SUCCEEDED,
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
            outputs_produced=["approval.json"],
        ),
    )

    # Second pass: should advance through downstream
    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
    await driver.run()
    assert _read_job_config(job_dir).state == JobState.COMPLETED


async def test_succeeded_without_required_outputs_fails_job(tmp_path: Path) -> None:
    """Runner returns succeeded=True but no outputs → job FAILS, not COMPLETES.

    Codex-review (Critical): driver must validate required_outputs.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-a", required_outputs=["must-exist.md"])])
    # Fixture says succeeded but writes NOTHING
    _write_fixture(fixtures_dir, "stage-a", {"outcome": "succeeded"})

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
    await driver.run()

    cfg = _read_job_config(job_dir)
    assert cfg.state == JobState.FAILED, "missing required output must FAIL the job"


async def test_runner_exception_fails_job(tmp_path: Path) -> None:
    """If the stage runner raises, the job is left FAILED (not in STAGES_RUNNING).

    Codex-review (Important): heartbeat would otherwise stop while
    job.json still says STAGES_RUNNING.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-a", required_outputs=["x.txt"])])

    class ExplodingRunner:
        async def run(self, stage_def, job_dir_arg, stage_run_dir):
            raise RuntimeError("boom")

    driver = JobDriver(
        job_dir.name,
        root=tmp_path,
        stage_runner=ExplodingRunner(),  # type: ignore[arg-type]
        heartbeat_interval=0.1,
    )
    await driver.run()

    cfg = _read_job_config(job_dir)
    assert cfg.state == JobState.FAILED
    sr = _read_stage_run(job_dir, "stage-a")
    assert sr is not None
    assert sr.state == StageState.FAILED


async def test_missing_required_inputs_fails_stage(tmp_path: Path) -> None:
    """Stage whose required input is missing on disk → job FAILS.

    Codex-review (P2): _inputs_ready() must gate stage execution.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    # stage-b requires a.txt as input but no producer exists for it
    _write_stage_list(
        job_dir,
        [_make_stage("stage-b", required_outputs=["b.txt"], required_inputs=["a.txt"])],
    )
    _write_fixture(
        fixtures_dir, "stage-b", {"outcome": "succeeded", "artifacts": {"b.txt": "would write"}}
    )

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
    await driver.run()

    assert _read_job_config(job_dir).state == JobState.FAILED


async def test_event_seq_resumes_after_restart(tmp_path: Path) -> None:
    """A restarted driver resumes seq numbers from existing events.jsonl.

    Codex-review (Important): re-emitting seq=0 violates monotonic-seq spec.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-a", required_outputs=["a.txt"])])
    _write_fixture(fixtures_dir, "stage-a", {"outcome": "succeeded", "artifacts": {"a.txt": "x"}})

    # First run produces some events
    driver1 = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
    await driver1.run()
    events_path = paths.job_events_jsonl(job_dir.name, root=tmp_path)
    seqs_before = [
        json.loads(line)["seq"] for line in events_path.read_text().splitlines() if line.strip()
    ]
    assert seqs_before == sorted(set(seqs_before)) and seqs_before == list(
        range(min(seqs_before), max(seqs_before) + 1)
    )

    # Spawn a second driver (same job dir). Since job is COMPLETED it won't
    # emit stage events, but JobDriver.run() emits the initial state-resume
    # transition. The seq must be > max seqs_before.
    driver2 = JobDriver(
        job_dir.name,
        root=tmp_path,
        stage_runner=FakeStageRunner(fixtures_dir),
        heartbeat_interval=0.1,
    )
    assert driver2._seq == max(seqs_before) + 1, (
        f"_seq should resume from {max(seqs_before) + 1}, got {driver2._seq}"
    )


async def test_event_seq_tolerates_truncated_tail(tmp_path: Path) -> None:
    """Corrupt/truncated events.jsonl tail is skipped; seq still resumes.

    Codex-review (Important).
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-a", required_outputs=["a.txt"])])

    # Pre-seed events.jsonl with valid + corrupt lines
    events_path = paths.job_events_jsonl(job_dir.name, root=tmp_path)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        '{"seq": 0, "event_type": "x", "source": "job_driver", "job_id": "j", "stage_id": null, '
        '"timestamp": "2026-01-01T00:00:00+00:00", "payload": {}}\n'
        '{"seq": 1, "event_type": "x", "source": "job_driver", "job_id": "j", "stage_id": null, '
        '"timestamp": "2026-01-01T00:00:00+00:00", "payload": {}}\n'
        '{"seq": 2, "event_type":\n'  # truncated mid-line
    )

    driver = JobDriver(
        job_dir.name,
        root=tmp_path,
        stage_runner=FakeStageRunner(fixtures_dir),
        heartbeat_interval=0.1,
    )
    assert driver._seq == 2, "seq should resume from max valid+1, ignoring corrupt tail"


async def test_loop_back_preserves_verdict_artifact(tmp_path: Path) -> None:
    """Loop-back preserves the verdict-producing stage's output (the writer's feedback).

    Codex-review (Important): clearing the verdict drops the routing signal
    the writer needs to revise.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    stages = [
        _make_stage("write-spec", required_outputs=["spec.md"]),
        _make_stage(
            "review-spec",
            required_outputs=["spec-review.json"],
            required_inputs=["spec.md"],
            loop_back=LoopBack(
                to="write-spec",
                condition="not spec-review.json.approved",
                max_iterations=2,
                on_exhaustion=OnExhaustion(kind="hil-manual-step", prompt="x"),
            ),
        ),
    ]
    _write_job_config(job_dir)
    _write_stage_list(job_dir, stages)

    # First review: approved=False (so loop_back fires); second: approved=True.
    review_attempt = {"n": 0}
    seen_inputs_to_writer: list[bool] = []
    original_run = FakeStageRunner.run

    async def _scripted_run(self, stage_def, job_dir_arg, stage_run_dir):
        if stage_def.id == "write-spec":
            # Snapshot whether the verdict file is visible to the writer
            verdict_path = job_dir_arg / "spec-review.json"
            seen_inputs_to_writer.append(verdict_path.exists())
            (job_dir_arg / "spec.md").write_text("draft")
            return StageResult(succeeded=True, outputs_produced=["spec.md"])
        if stage_def.id == "review-spec":
            review_attempt["n"] += 1
            payload = {"approved": review_attempt["n"] >= 2}
            (job_dir_arg / "spec-review.json").write_text(json.dumps(payload))
            return StageResult(succeeded=True, outputs_produced=["spec-review.json"])
        return await original_run(self, stage_def, job_dir_arg, stage_run_dir)

    FakeStageRunner.run = _scripted_run  # type: ignore[method-assign]
    try:
        driver = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
        await driver.run()
    finally:
        FakeStageRunner.run = original_run  # type: ignore[method-assign]

    assert _read_job_config(job_dir).state == JobState.COMPLETED
    # First write: no prior verdict. Second write (after loop_back):
    # the rejected verdict MUST be visible to the writer.
    assert seen_inputs_to_writer == [False, True], (
        f"verdict must be preserved across loop_back; got {seen_inputs_to_writer}"
    )


async def test_loop_back_exhaustion_blocks_on_human(tmp_path: Path) -> None:
    """When loop_back.max_iterations is exhausted, job → BLOCKED_ON_HUMAN.

    Codex-review (Important): on_exhaustion.kind=hil-manual-step must
    actually block, not silently advance.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    stages = [
        _make_stage("write-spec", required_outputs=["spec.md"]),
        _make_stage(
            "review-spec",
            required_outputs=["spec-review.json"],
            required_inputs=["spec.md"],
            loop_back=LoopBack(
                to="write-spec",
                condition="not spec-review.json.approved",
                max_iterations=1,
                on_exhaustion=OnExhaustion(
                    kind="hil-manual-step",
                    prompt="Spec keeps getting rejected; please intervene.",
                ),
            ),
        ),
    ]
    _write_job_config(job_dir)
    _write_stage_list(job_dir, stages)

    original_run = FakeStageRunner.run

    async def _always_reject(self, stage_def, job_dir_arg, stage_run_dir):
        if stage_def.id == "write-spec":
            (job_dir_arg / "spec.md").write_text("v")
            return StageResult(succeeded=True, outputs_produced=["spec.md"])
        if stage_def.id == "review-spec":
            (job_dir_arg / "spec-review.json").write_text('{"approved": false}')
            return StageResult(succeeded=True, outputs_produced=["spec-review.json"])
        return await original_run(self, stage_def, job_dir_arg, stage_run_dir)

    FakeStageRunner.run = _always_reject  # type: ignore[method-assign]
    try:
        driver = _make_driver(job_dir, fixtures_dir, root=tmp_path, heartbeat_interval=0.1)
        await driver.run()
    finally:
        FakeStageRunner.run = original_run  # type: ignore[method-assign]

    assert _read_job_config(job_dir).state == JobState.BLOCKED_ON_HUMAN


async def test_runner_required_when_starting(tmp_path: Path) -> None:
    """A driver constructed with no stage_runner must refuse to run.

    Codex-review (P1): otherwise the assert fires mid-stage and leaves
    the job stuck in STAGES_RUNNING.
    """
    import pytest

    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)

    _write_job_config(job_dir)
    _write_stage_list(job_dir, [_make_stage("stage-a", required_outputs=["a.txt"])])

    driver = JobDriver(job_dir.name, root=tmp_path, stage_runner=None, heartbeat_interval=0.1)
    with pytest.raises(RuntimeError, match="stage_runner"):
        await driver.run()

    # Job state must NOT have transitioned
    assert _read_job_config(job_dir).state == JobState.SUBMITTED


async def test_latest_symlink_atomic_replace(tmp_path: Path) -> None:
    """Re-running a stage replaces the `latest` symlink atomically.

    Codex-review (Minor): unlink-then-symlink left a no-latest window.
    Now we use os.replace() with a temp symlink.
    """
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

    latest = paths.stage_run_latest(job_dir.name, "stage-a", root=tmp_path)
    assert latest.is_symlink()
    # No leftover .latest.tmp.* files
    leftovers = list(latest.parent.glob(".latest.tmp.*"))
    assert leftovers == [], f"leftover temp symlinks: {leftovers}"


# ---------------------------------------------------------------------------
# Stage 12.5 (A6) — predicate error policy
#
# Pre-12.5 ``runs_if`` defaulted to True on PredicateError (run on uncertainty)
# while ``loop_back.condition`` defaulted to False (don't loop on uncertainty).
# The asymmetry was undocumented and likely accidental.  Stage 12.5 unifies
# both to default-False on PredicateError — skip-on-uncertainty is the safer
# side for both branches: a stage that should have run gets skipped (visible
# in the next stage's missing-input failure) rather than running with stale
# context, and a loop that should have run gets ended (terminating progress)
# rather than running forever on broken context.
# ---------------------------------------------------------------------------


async def test_runs_if_eval_error_skips_stage(tmp_path: Path) -> None:
    """A runs_if predicate that references a missing artifact raises
    PredicateError at evaluation time; the stage must be SKIPPED, not run.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    # The runs_if predicate references an artifact that does NOT exist on
    # disk; evaluate_predicate will raise PredicateError when it tries to
    # resolve the dotted path.
    stages = [
        _make_stage(
            "stage-with-bad-predicate",
            required_outputs=["should-not-be-written.txt"],
            runs_if="missing.json.verdict == 'approved'",
        ),
        # A trailing stage so the job has something to complete with —
        # otherwise the final-outputs check fails with "no outputs".
        _make_stage("final-stage", required_outputs=["final.txt"]),
    ]
    _write_job_config(job_dir)
    _write_stage_list(job_dir, stages)
    _write_fixture(
        fixtures_dir,
        "final-stage",
        {"outcome": "succeeded", "artifacts": {"final.txt": "done"}},
    )
    # No fixture for the bad-predicate stage — if runs_if defaulted-True,
    # the runner would try to dispatch FakeStageRunner which would fail
    # to find the fixture; we'd get a runner exception, not a clean skip.

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
    await driver.run()

    # The stage was skipped — no stage.json, no output written.
    assert not (job_dir / "stages" / "stage-with-bad-predicate" / "stage.json").exists()
    assert not (job_dir / "should-not-be-written.txt").exists()
    # Job completes — skipping is a normal flow when paired with a real
    # downstream stage.
    assert _read_job_config(job_dir).state == JobState.COMPLETED


async def test_loop_back_condition_eval_error_does_not_loop(tmp_path: Path) -> None:
    """A loop_back.condition predicate that raises PredicateError must NOT
    loop — same default-False policy as runs_if.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    stages = [
        _make_stage("write-spec", required_outputs=["spec.md"]),
        _make_stage(
            "review-spec",
            required_outputs=["spec-review.json"],
            required_inputs=["spec.md"],
            loop_back=LoopBack(
                to="write-spec",
                # Condition references an artifact that doesn't exist —
                # evaluate_predicate will raise PredicateError.
                condition="totally-missing.json.verdict == 'rejected'",
                max_iterations=3,
                on_exhaustion=OnExhaustion(
                    kind="hil-manual-step",
                    prompt="exhausted",
                ),
            ),
        ),
    ]
    _write_job_config(job_dir)
    _write_stage_list(job_dir, stages)
    _write_fixture(
        fixtures_dir, "write-spec", {"outcome": "succeeded", "artifacts": {"spec.md": "v1"}}
    )
    _write_fixture(
        fixtures_dir,
        "review-spec",
        # Note: no totally-missing.json artifact written
        {"outcome": "succeeded", "artifacts": {"spec-review.json": '{"verdict":"rejected"}'}},
    )

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
    await driver.run()

    # write-spec must have run exactly once — predicate-eval-error means
    # don't loop, so we never re-enter the writer.
    assert _read_job_config(job_dir).state == JobState.COMPLETED
    write_run_dir = job_dir / "stages" / "write-spec"
    runs = sorted(p.name for p in write_run_dir.iterdir() if p.is_dir())
    # Only run-1 should exist; if we'd looped, run-2 would also exist.
    assert "run-1" in runs
    assert "run-2" not in runs


async def test_completion_does_not_silently_exempt_actually_run_stage(tmp_path: Path) -> None:
    """Stage 12.5 (A6 follow-up after Codex review): a stage that actually
    ran at dispatch must NOT be exempted from the final-outputs check just
    because a re-evaluation of its ``runs_if`` predicate would now fail.

    Scenario: stage-A's runs_if references foo.json (which exists at
    dispatch); stage-A runs and is supposed to write a.txt; we then delete
    a.txt before completion.  If the final-outputs check naively
    re-evaluated runs_if and treated PredicateError as "skipped", a
    deletion of foo.json instead would mask the real integrity failure.
    Here we use a stable predicate so dispatch sees True, the stage runs,
    we then delete its output and assert completion FAILS — the dispatch
    decision (ran) trumps any later predicate state.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    # Pre-seed an artifact so the predicate evaluates to True at dispatch
    seed = job_dir / "seed.json"
    seed.parent.mkdir(parents=True, exist_ok=True)
    seed.write_text('{"flag": "go"}')

    stages = [
        _make_stage(
            "stage-a",
            required_outputs=["a.txt"],
            runs_if="seed.json.flag == 'go'",
        ),
    ]
    _write_job_config(job_dir)
    _write_stage_list(job_dir, stages)

    # Custom fixture: stage runs successfully, writes a.txt, then we delete
    # it AFTER the stage completes but BEFORE final-outputs check fires.
    # We achieve that by patching the FakeStageRunner to delete the output
    # right after writing it.
    _write_fixture(fixtures_dir, "stage-a", {"outcome": "succeeded", "artifacts": {"a.txt": "x"}})

    original_run = FakeStageRunner.run

    async def patched_run(self: FakeStageRunner, stage_def, job_dir_, *args, **kwargs):  # type: ignore[no-untyped-def]
        result = await original_run(self, stage_def, job_dir_, *args, **kwargs)
        # Simulate the artifact being deleted out from under us — a race,
        # a human cleanup, a buggy hook, etc.
        out = job_dir_ / "a.txt"
        if out.exists():
            out.unlink()
        return result

    try:
        FakeStageRunner.run = patched_run  # type: ignore[method-assign]
        driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
        await driver.run()
    finally:
        FakeStageRunner.run = original_run  # type: ignore[method-assign]

    # The stage actually ran (its stage.json exists with SUCCEEDED), but the
    # required output is now missing.  Per Stage 12.5 (A6 follow-up), this
    # must FAIL completion — the dispatch decision says "ran", so the
    # outputs are required.  Pre-fix this would have silently exempted the
    # stage if the seed artifact had also vanished and we'd re-evaluated
    # the predicate at completion.
    assert _read_job_config(job_dir).state == JobState.FAILED, (
        "stage that actually ran must not be exempted from final-outputs check"
    )


# ---------------------------------------------------------------------------
# Stage 12.5 (A2) — artifact validator enforcement
# ---------------------------------------------------------------------------


async def test_stage_fails_when_non_empty_validator_rejects_empty_output(
    tmp_path: Path,
) -> None:
    """A stage that reports success but writes an empty file must FAIL when
    the output declares ``validators: ["non-empty"]``.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    stage = StageDefinition(
        id="write-doc",
        worker="agent",  # type: ignore[arg-type]
        inputs=InputSpec(),
        outputs=OutputSpec(required=["doc.md"]),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(
            required_outputs=[RequiredOutput(path="doc.md", validators=["non-empty"])]
        ),
    )
    _write_stage_list(job_dir, [stage])
    # Fixture reports success but writes an empty file.
    _write_fixture(fixtures_dir, "write-doc", {"outcome": "succeeded", "artifacts": {"doc.md": ""}})

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
    await driver.run()

    assert _read_job_config(job_dir).state == JobState.FAILED


async def test_stage_succeeds_when_non_empty_validator_passes(tmp_path: Path) -> None:
    """A stage that writes non-empty content must SUCCEED when the output
    declares ``validators: ["non-empty"]``.
    """
    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    stage = StageDefinition(
        id="write-doc",
        worker="agent",  # type: ignore[arg-type]
        inputs=InputSpec(),
        outputs=OutputSpec(required=["doc.md"]),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(
            required_outputs=[RequiredOutput(path="doc.md", validators=["non-empty"])]
        ),
    )
    _write_stage_list(job_dir, [stage])
    _write_fixture(
        fixtures_dir, "write-doc", {"outcome": "succeeded", "artifacts": {"doc.md": "The design."}}
    )

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
    await driver.run()

    assert _read_job_config(job_dir).state == JobState.COMPLETED


async def test_artifact_validator_rejects_invalid_review_verdict(tmp_path: Path) -> None:
    """A stage with ``artifact_validators: [{path: ..., schema: review-verdict-schema}]``
    must FAIL when the file does not conform to the ReviewVerdict schema.
    """
    import json as _json

    jobs_dir = tmp_path / "jobs"
    job_dir = jobs_dir / "test-job"
    job_dir.mkdir(parents=True)
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()

    _write_job_config(job_dir)
    stage = StageDefinition(
        id="review",
        worker="agent",  # type: ignore[arg-type]
        inputs=InputSpec(),
        outputs=OutputSpec(required=["review.json"]),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(
            required_outputs=[RequiredOutput(path="review.json")],
            artifact_validators=[
                ArtifactValidator(**{"path": "review.json", "schema": "review-verdict-schema"})
            ],
        ),
    )
    _write_stage_list(job_dir, [stage])
    # The file exists but has wrong shape — verdict value is not in the allowed set.
    _write_fixture(
        fixtures_dir,
        "review",
        {
            "outcome": "succeeded",
            "artifacts": {
                "review.json": _json.dumps(
                    {
                        "verdict": "maybe",
                        "summary": "x",
                        "unresolved_concerns": [],
                        "addressed_in_this_iteration": [],
                    }
                )
            },
        },
    )

    driver = _make_driver(job_dir, fixtures_dir, root=tmp_path)
    await driver.run()

    assert _read_job_config(job_dir).state == JobState.FAILED
