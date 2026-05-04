"""Job Driver — deterministic state-machine executor.

Per design doc § Lifecycle — three nested state machines and
implementation.md § Stage 4.

``JobDriver`` is spawned once per active job (as a subprocess via
``job_driver.__main__``). It:

1. Reads the compiled ``stage-list.yaml`` from the job dir.
2. Transitions ``job.json`` from ``SUBMITTED`` to ``STAGES_RUNNING``.
3. Iterates stages in order:
   - Skips stages whose ``runs_if`` predicate evaluates to false.
   - Skips stages that already SUCCEEDED (resume after crash).
   - For ``worker: human`` stages with outputs missing: writes
     ``BLOCKED_ON_HUMAN`` and exits cleanly so the dashboard can resume
     the driver after the human action lands on disk.
   - Runs the ``StageRunner`` (FakeStageRunner in Stage 4; RealStageRunner in
     Stage 5).
   - Validates ``exit_condition.required_outputs`` after success.
   - Handles ``loop_back``: if the condition holds and the attempt counter
     is within ``max_iterations``, re-runs the target stage. The verdict
     artifact (the looping stage's output) is preserved as feedback for the
     writer; only the target-range writer outputs are cleared.
   - On ``loop_back.max_iterations`` exhaustion: writes ``BLOCKED_ON_HUMAN``
     per ``on_exhaustion: hil-manual-step``.
4. When all stages are done with all final outputs present:
   ``STAGES_RUNNING`` → ``COMPLETED``.
5. On unrecoverable failure (stage failed, runner exception, missing
   required output): ``STAGES_RUNNING`` → ``FAILED``.
6. On SIGTERM or command-file cancel: ``STAGES_RUNNING`` → ``ABANDONED``;
   active stage → ``CANCELLED``.
7. Writes a heartbeat file every ``heartbeat_interval`` seconds (default 30).

Event sequence numbers persist across driver restarts: on startup the driver
scans existing ``events.jsonl`` and resumes from ``max(seq) + 1`` so a
restarted driver never duplicates seq values in the same job log.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from job_driver.stage_runner import StageResult, StageRunner
from shared import paths
from shared.atomic import atomic_append_jsonl, atomic_write_json
from shared.models.events import Event
from shared.models.job import JobConfig, JobState
from shared.models.stage import StageDefinition, StageRun, StageState
from shared.predicate import PredicateError, evaluate_predicate

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL: float = 30.0
COMMAND_POLL_INTERVAL: float = 2.0


class JobDriver:
    """Executes a job's stage list deterministically.

    Parameters
    ----------
    job_slug:
        Identifies the job dir under ``<root>/jobs/<slug>/``.
    root:
        Override for the hammock root (``~/.hammock/`` by default).
    stage_runner:
        Stage execution backend. Must be supplied; ``run()`` raises
        ``RuntimeError`` if missing so the driver fails fast before
        changing job state.
    heartbeat_interval:
        Seconds between heartbeat touches (default 30).
    now_fn:
        Injectable clock for tests (defaults to ``datetime.now(UTC)``).
    """

    def __init__(
        self,
        job_slug: str,
        *,
        root: Path | None = None,
        stage_runner: StageRunner | None = None,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.job_slug = job_slug
        self.root = root
        self._stage_runner = stage_runner
        self.heartbeat_interval = heartbeat_interval
        self._now: Callable[[], datetime] = now_fn or (lambda: datetime.now(UTC))
        self._cancel_event = asyncio.Event()
        self._seq = self._initial_seq()

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main loop: run until terminal/blocked state or cancellation."""
        if self._stage_runner is None:
            raise RuntimeError(
                "JobDriver requires a stage_runner; refusing to start "
                "(would otherwise crash mid-stage and leave the job in "
                "STAGES_RUNNING)."
            )

        loop = asyncio.get_event_loop()

        # Install SIGTERM handler to trigger graceful shutdown
        def _sigterm_handler() -> None:
            log.info("SIGTERM received — initiating cancellation")
            self._cancel_event.set()

        loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)

        try:
            # Transition SUBMITTED → STAGES_RUNNING (idempotent: only fires
            # if state is SUBMITTED; on resume, state is already
            # STAGES_RUNNING/BLOCKED_ON_HUMAN and we leave it alone).
            job_cfg = self._read_job_config()
            if job_cfg.state == JobState.SUBMITTED:
                self._write_job_state(JobState.STAGES_RUNNING)
            elif job_cfg.state == JobState.BLOCKED_ON_HUMAN:
                # Resume from human-block: re-enter STAGES_RUNNING.
                # _execute_stages will skip already-resolved human stages.
                self._write_job_state(JobState.STAGES_RUNNING)

            # Touch heartbeat at startup
            self._touch_heartbeat()

            # Start background tasks
            hb_task = asyncio.create_task(self._heartbeat_loop())
            poll_task = asyncio.create_task(self._command_poll_loop())

            try:
                await self._execute_stages()
            finally:
                hb_task.cancel()
                poll_task.cancel()
                await asyncio.gather(hb_task, poll_task, return_exceptions=True)

        finally:
            with contextlib.suppress(Exception):
                loop.remove_signal_handler(signal.SIGTERM)

    # ------------------------------------------------------------------
    # Stage execution loop
    # ------------------------------------------------------------------

    # P3 (real-claude e2e precondition track): expander stages append
    # new entries to ``stage-list.yaml`` at runtime; the driver must
    # re-read after each successful expander so the appended stages
    # actually execute. Cap the total stages a single job can reach
    # so a runaway expander surfaces as a hard error rather than an
    # infinite loop.
    _MAX_STAGES_PER_JOB: int = 1000

    async def _execute_stages(self) -> None:
        stages = self._read_stages()
        # loop_back iteration counters: keyed by (review_stage_id, target_stage_id)
        loop_counters: dict[tuple[str, str], int] = {}
        # Stage 12.5 (A6 follow-up): track stages skipped at dispatch so the
        # final-outputs check can exempt only those (and not silently exempt
        # a stage that actually ran but whose predicate artifact later
        # disappeared — that would mask a real integrity failure).
        dispatch_skipped: set[str] = set()

        i = 0
        while i < len(stages):
            if self._cancel_event.is_set():
                self._write_job_state(JobState.ABANDONED)
                return

            stage_def = stages[i]

            # Check runs_if predicate.  Stage 12.5 (A6): unified policy —
            # PredicateError defaults to False (skip-on-uncertainty) for both
            # runs_if and loop_back.condition.  A predicate that compiled but
            # failed at evaluation is a real bug somewhere upstream; logging
            # at error makes it visible.  Skipping is the safer default: the
            # next stage's missing-input check will surface the issue, rather
            # than running a stage with stale or wrong context.
            if stage_def.runs_if is not None:
                try:
                    ctx = self._build_predicate_context()
                    should_run = evaluate_predicate(stage_def.runs_if, ctx)
                except PredicateError as exc:
                    log.error(
                        "runs_if eval error for %s: %s — defaulting to False (skip)",
                        stage_def.id,
                        exc,
                    )
                    should_run = False
                if not should_run:
                    log.info("Skipping stage %s (runs_if=false)", stage_def.id)
                    dispatch_skipped.add(stage_def.id)
                    i += 1
                    continue

            # Skip if stage already SUCCEEDED (resume) — requires BOTH
            # stage.json SUCCEEDED AND outputs present, so we never treat
            # stray output files from a crashed stage as completion.
            if self._stage_already_succeeded(stage_def):
                log.info("Skipping stage %s (already SUCCEEDED)", stage_def.id)
                i += 1
                continue

            # Human/HIL gate: the Job Driver does not run human stages — it
            # blocks the job, writes BLOCKED_ON_HUMAN, and exits cleanly.
            # The dashboard re-spawns the driver once outputs land.
            if stage_def.worker == "human":
                self._block_on_human(stage_def, reason="human-stage")
                return

            # Required inputs must be present before running. If a producer
            # was skipped by runs_if (or an artifact was deleted before
            # resume), the stage is not READY — fail the job rather than
            # running with missing inputs and silently producing nothing.
            if not self._inputs_ready(stage_def):
                log.error(
                    "Stage %s required inputs missing: %s",
                    stage_def.id,
                    [
                        inp
                        for inp in stage_def.inputs.required
                        if not (self._job_dir() / inp).exists()
                    ],
                )
                self._fail_stage(
                    stage_def,
                    reason="required inputs missing",
                )
                self._write_job_state(JobState.FAILED)
                return

            # Run the stage (catching runner exceptions so the job is always
            # left in a terminal state on failure).
            try:
                result = await self._run_single_stage(stage_def)
            except Exception as exc:
                log.exception("Stage %s runner raised: %s", stage_def.id, exc)
                self._fail_stage(stage_def, reason=f"runner exception: {exc}")
                self._write_job_state(JobState.FAILED)
                return

            if result is None:
                # Cancelled mid-stage
                self._write_job_state(JobState.ABANDONED)
                return

            if not result.succeeded:
                self._write_job_state(JobState.FAILED)
                return

            # Validate required_outputs are present after success. This is
            # the SUCCEEDED gate per design § Stage state machine — a runner
            # may return succeeded=True without actually writing outputs.
            missing = self._missing_required_outputs(stage_def)
            if missing:
                log.error(
                    "Stage %s reported success but required outputs missing: %s",
                    stage_def.id,
                    missing,
                )
                self._fail_stage(
                    stage_def,
                    reason=f"required outputs missing after success: {missing}",
                )
                self._write_job_state(JobState.FAILED)
                return

            # Stage 12.5 (A2): run named validators on outputs after existence check.
            validator_errors = self._run_output_validators(stage_def)
            if validator_errors:
                log.error(
                    "Stage %s failed artifact validation: %s",
                    stage_def.id,
                    validator_errors,
                )
                self._fail_stage(
                    stage_def,
                    reason=f"artifact validation failed: {validator_errors}",
                )
                self._write_job_state(JobState.FAILED)
                return

            # Handle loop_back
            if stage_def.loop_back is not None:
                lb = stage_def.loop_back
                key = (stage_def.id, lb.to)
                try:
                    ctx = self._build_predicate_context()
                    condition_holds = evaluate_predicate(lb.condition, ctx)
                except PredicateError as exc:
                    # Stage 12.5 (A6): unified default-False policy.  Same
                    # rationale as runs_if above — terminate progress on
                    # uncertainty rather than loop forever on broken context.
                    log.error("loop_back condition eval error: %s — not looping", exc)
                    condition_holds = False

                if condition_holds:
                    current_count = loop_counters.get(key, 0)
                    if current_count < lb.max_iterations:
                        loop_counters[key] = current_count + 1
                        log.info(
                            "loop_back: %s → %s (iteration %d/%d)",
                            stage_def.id,
                            lb.to,
                            loop_counters[key],
                            lb.max_iterations,
                        )
                        # Find target stage index and resume from there.
                        target_idx = next((j for j, s in enumerate(stages) if s.id == lb.to), None)
                        if target_idx is not None:
                            # Clear stage.json + outputs for [target_idx, i) so
                            # those stages re-run from scratch. For the
                            # verdict-producing stage at i, reset only its
                            # stage.json (so it re-runs to evaluate the new
                            # spec) — its OUTPUT file is the writer's
                            # feedback and must be preserved through the
                            # next iteration.
                            self._clear_stage_outputs(stages[target_idx:i])
                            self._reset_stage_run(stage_def)
                            i = target_idx
                            continue
                    else:
                        log.warning(
                            "loop_back max_iterations=%d exhausted for %s → %s — "
                            "transitioning to BLOCKED_ON_HUMAN",
                            lb.max_iterations,
                            stage_def.id,
                            lb.to,
                        )
                        self._block_on_human(
                            stage_def,
                            reason=f"loop_back max_iterations exhausted ({stage_def.id} → {lb.to})",
                        )
                        return

            # P3: dynamic expansion. After a successful expander stage,
            # re-read stage-list.yaml so any newly-appended entries get
            # picked up. Expanders may only append; they must not
            # reorder or remove existing entries (the loop trusts
            # per-stage state for "is this done?", not list order, so
            # removed entries are inert — but reordering would break
            # loop_back targeting). The cap is enforced inside
            # _read_stages so a runaway expander never reaches Pydantic.
            if stage_def.is_expander:
                try:
                    stages = self._read_stages()
                except ValueError as exc:
                    log.error("expander rewrite rejected: %s", exc)
                    self._fail_stage(
                        stage_def,
                        reason=f"runaway expander: {exc}",
                    )
                    self._write_job_state(JobState.FAILED)
                    return
            i += 1

        # All stages done — verify final-stage outputs exist before COMPLETED.
        if not self._cancel_event.is_set():
            final_missing = self._missing_final_outputs(stages, skipped=dispatch_skipped)
            if final_missing:
                log.error("Cannot COMPLETE: final outputs missing: %s", final_missing)
                self._write_job_state(JobState.FAILED)
                return
            self._write_job_state(JobState.COMPLETED)

    async def _run_single_stage(self, stage_def: StageDefinition) -> StageResult | None:
        """Execute one stage: create stage dir, run, persist state.

        Returns ``None`` if the run was interrupted by a cancellation request.
        """
        assert self._stage_runner is not None  # checked in run()

        job_dir = self._job_dir()
        attempt = self._next_attempt(stage_def.id)

        stage_run_dir = paths.stage_run_dir(self.job_slug, stage_def.id, attempt, root=self.root)
        stage_run_dir.mkdir(parents=True, exist_ok=True)

        # Update latest symlink atomically — create a new symlink under a
        # temp name and os.replace() it over latest, so readers never see a
        # missing latest.
        self._update_latest_symlink(stage_def.id, stage_run_dir.name)

        # v0 alignment Plan #2 + #8: pre-stage isolation. Create the
        # stage branch off the job branch, then check it out into a
        # worktree under <job_dir>/stages/<sid>/worktree/. Best-effort:
        # if the project repo isn't a real git repo, skip isolation
        # and run the stage in-place (fake-fixture flows; tests).
        self._setup_stage_isolation(stage_def.id)

        # v0 alignment Plan #3: materialise the project's specialist
        # catalogue into <stage_run_dir>/agents.json. RealStageRunner
        # passes its contents to claude via `--agents <inline json>`.
        # Best-effort: failure logs but never aborts the stage.
        self._materialise_specialists_for_stage(stage_run_dir)

        # Persist RUNNING state
        stage_run = StageRun(
            stage_id=stage_def.id,
            attempt=attempt,
            state=StageState.RUNNING,
            started_at=self._now(),
        )
        self._write_stage_run(stage_def.id, stage_run)
        self._emit_event(
            "stage_state_transition",
            stage_id=stage_def.id,
            payload={"from": "PENDING", "to": "RUNNING", "attempt": attempt},
        )

        log.info("Running stage %s (attempt %d)", stage_def.id, attempt)

        # Race the stage runner against the cancel event AND the
        # wall-clock budget cap (v0 alignment Plan #1). The runner is
        # the worker; the parent process holds the kill switch, so
        # workers cannot disable their own budgets per design.
        #
        # Tie-breaking policy (Codex review LOW 4): when stage and
        # wall-clock complete simultaneously, **wall-clock wins** —
        # the wall-clock branch is checked before the stage-result
        # branch below. Rationale: a stage that just barely beat the
        # cap is indistinguishable from one that just barely missed it
        # by clock skew; safer to fail-closed on the cap than to admit
        # a marginal overrun.
        stage_task = asyncio.create_task(self._stage_runner.run(stage_def, job_dir, stage_run_dir))
        cancel_task = asyncio.create_task(self._cancel_event.wait())
        wall_clock_task: asyncio.Task[None] | None = None
        wait_set: set[asyncio.Task[Any]] = {stage_task, cancel_task}
        if stage_def.budget.max_wall_clock_min is not None:
            wall_clock_seconds = float(stage_def.budget.max_wall_clock_min) * 60.0
            wall_clock_task = asyncio.create_task(asyncio.sleep(wall_clock_seconds))
            wait_set.add(wall_clock_task)

        done, pending = await asyncio.wait(
            wait_set,
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        if wall_clock_task is not None and wall_clock_task in done:
            # Wall-clock cap fired before the stage finished. Mark the
            # stage FAILED with a structured budget-overrun reason.
            stage_run = stage_run.model_copy(
                update={
                    "state": StageState.FAILED,
                    "ended_at": self._now(),
                    "restart_count": attempt - 1,
                }
            )
            self._write_stage_run(stage_def.id, stage_run)
            self._emit_event(
                "stage_state_transition",
                stage_id=stage_def.id,
                payload={
                    "from": "RUNNING",
                    "to": "FAILED",
                    "reason": "budget overrun (max_wall_clock_min)",
                },
            )
            self._write_manifest_for_latest_run(stage_def.id)
            # P4: emit worker_exit so the wall-clock-killed subprocess
            # leaves a trail for the e2e test (and operators).
            self._emit_event(
                "worker_exit",
                stage_id=stage_def.id,
                payload={
                    "stage_id": stage_def.id,
                    "exit_code": None,
                    "succeeded": False,
                    "reason": "budget overrun (max_wall_clock_min)",
                },
            )
            return StageResult(succeeded=False, reason="budget overrun (max_wall_clock_min)")

        if cancel_task in done:
            # Cancellation won — clean up stage state
            stage_run = stage_run.model_copy(
                update={"state": StageState.CANCELLED, "ended_at": self._now()}
            )
            self._write_stage_run(stage_def.id, stage_run)
            self._emit_event(
                "stage_state_transition",
                stage_id=stage_def.id,
                payload={"from": "RUNNING", "to": "CANCELLED"},
            )
            return None

        # If the runner raised, propagate so the caller can fail the job.
        # Codex review on PR #28: emit worker_exit with succeeded=False
        # before re-raising — otherwise a crashing subprocess leaves no
        # trail in events.jsonl (the e2e test contract is "every claude
        # subprocess that ran emitted a worker_exit event").
        try:
            result: StageResult = stage_task.result()
        except BaseException as exc:
            self._emit_event(
                "worker_exit",
                stage_id=stage_def.id,
                payload={
                    "stage_id": stage_def.id,
                    "exit_code": None,
                    "succeeded": False,
                    "reason": f"runner exception: {exc!r}",
                },
            )
            raise

        # Budget post-check: a runner that reports success but blew the
        # spend cap is still a budget-overrun failure (workers cannot
        # disable their own budgets). claude --max-budget-usd is the
        # primary defence inside RealStageRunner; this catches the
        # remainder (FakeStageRunner runs and any Real-runner overshoot
        # claude couldn't preempt).
        if (
            stage_def.budget.max_budget_usd is not None
            and result.cost_usd > stage_def.budget.max_budget_usd
        ):
            result = StageResult(
                succeeded=False,
                reason=(
                    f"budget overrun (max_budget_usd: spent ${result.cost_usd:.4f}, "
                    f"cap ${stage_def.budget.max_budget_usd:.4f})"
                ),
                outputs_produced=result.outputs_produced,
                cost_usd=result.cost_usd,
            )

        final_state = StageState.SUCCEEDED if result.succeeded else StageState.FAILED
        stage_run = stage_run.model_copy(
            update={
                "state": final_state,
                "ended_at": self._now(),
                "outputs_produced": result.outputs_produced,
                "cost_accrued": result.cost_usd,
                "restart_count": attempt - 1,
            }
        )
        self._write_stage_run(stage_def.id, stage_run)

        # Run-archive integrity manifest (v0 alignment Plan #5; centralised
        # via _write_manifest_for_latest_run after Codex review of PR #23
        # so exception-routed failures via _fail_stage land one too).
        self._write_manifest_for_latest_run(stage_def.id)
        self._emit_event(
            "stage_state_transition",
            stage_id=stage_def.id,
            payload={"from": "RUNNING", "to": final_state, "cost_usd": result.cost_usd},
        )
        # P4 (real-claude e2e precondition track): every successful or
        # failed runner return produces a worker_exit event. The exit
        # code is None for FakeStageRunner (no subprocess); RealStageRunner
        # populates it from proc.returncode. The e2e test asserts this
        # event has exit_code=0 for every agent stage that ran.
        worker_exit_payload: dict[str, Any] = {
            "stage_id": stage_def.id,
            "exit_code": result.exit_code,
            "succeeded": result.succeeded,
        }
        if result.reason:
            worker_exit_payload["reason"] = result.reason
        self._emit_event(
            "worker_exit",
            stage_id=stage_def.id,
            payload=worker_exit_payload,
        )

        if result.cost_usd > 0:
            # Per design doc § Observability § Event taxonomy: cost_accrued
            # payload uses ``delta_usd`` / ``delta_tokens`` / ``running_total``.
            # v0 driver only knows the per-stage USD delta; tokens and the
            # accumulator are reserved for v1+.  Stage 12.5 (A3) aligned this
            # key with both the spec and the projection reader, which had
            # silently been folding to 0 because driver, projection, and spec
            # all named the field differently.
            #
            # Codex review of PR #23: include ``agent_ref`` so the
            # ``by_agent`` rollup in JobCostSummary actually populates
            # (without it, every job's by_agent was structurally empty).
            payload: dict[str, Any] = {"delta_usd": result.cost_usd}
            if stage_def.agent_ref:
                payload["agent_ref"] = stage_def.agent_ref
            self._emit_event(
                "cost_accrued",
                stage_id=stage_def.id,
                payload=payload,
            )

        return result

    # ------------------------------------------------------------------
    # Background coroutines
    # ------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                self._touch_heartbeat()
                await asyncio.sleep(self.heartbeat_interval)
            except asyncio.CancelledError:
                break

    async def _command_poll_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(COMMAND_POLL_INTERVAL)
                if self._check_cancel_command():
                    self._cancel_event.set()
                    return
            except asyncio.CancelledError:
                break

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _job_dir(self) -> Path:
        return paths.job_dir(self.job_slug, root=self.root)

    def _read_job_config(self) -> JobConfig:
        return JobConfig.model_validate_json(
            paths.job_json(self.job_slug, root=self.root).read_text()
        )

    def _write_job_state(self, state: JobState) -> None:
        cfg = self._read_job_config()
        old_state = cfg.state
        updated = cfg.model_copy(update={"state": state})
        atomic_write_json(paths.job_json(self.job_slug, root=self.root), updated)
        self._emit_event(
            "job_state_transition",
            payload={"from": str(old_state), "to": str(state)},
        )
        # Terminal-state hook: persist JobCostSummary to job-summary.json.
        # Per `JobCostSummary` docstring + v0 alignment audit Plan #6 — the
        # file was promised by the model but never written. Lazy import
        # avoids a circular at module load (cost_summary imports `paths`,
        # which is fine; this just keeps the runner module surface tight).
        if state in (JobState.COMPLETED, JobState.FAILED, JobState.ABANDONED):
            from job_driver.cost_summary import write_job_summary

            try:
                write_job_summary(
                    self.job_slug,
                    job_id=updated.job_id,
                    project_slug=updated.project_slug,
                    root=self.root,
                    completed_at=self._now(),
                )
            except Exception as exc:
                log.warning("failed to write job-summary.json for %s: %s", self.job_slug, exc)

    def _read_stages(self) -> list[StageDefinition]:
        stage_list_path = paths.job_stage_list(self.job_slug, root=self.root)
        data = yaml.safe_load(stage_list_path.read_text()) or {}
        raw_stages = data.get("stages", []) or []
        # P3 (codex review on PR #26): bound the size BEFORE pydantic
        # validation. A runaway expander could otherwise append 50k
        # entries that all get parsed into StageDefinition models
        # before the post-loop guard fires. Raising the cap-check up
        # to read time costs one len() call and stops a real DoS path.
        if len(raw_stages) > self._MAX_STAGES_PER_JOB:
            raise ValueError(
                f"stage-list.yaml exceeds the {self._MAX_STAGES_PER_JOB}-stage cap "
                f"(found {len(raw_stages)}); refusing to parse"
            )
        return [StageDefinition.model_validate(s) for s in raw_stages]

    def _stage_already_succeeded(self, stage_def: StageDefinition) -> bool:
        """True only if both stage.json says SUCCEEDED and outputs exist.

        Resume safety: a crashed stage that wrote partial outputs but never
        reached SUCCEEDED must re-run, not be skipped.
        """
        sj = paths.stage_json(self.job_slug, stage_def.id, root=self.root)
        if not sj.exists():
            return False
        try:
            sr = StageRun.model_validate_json(sj.read_text())
        except (json.JSONDecodeError, ValidationError, OSError) as exc:
            # Stage 12.5 (A7): narrow + log.  An unreadable stage.json is
            # treated as not-yet-succeeded so the driver re-runs the stage —
            # but we now log the cause so corruption / schema drift is
            # visible rather than silently swallowed.
            log.warning("stage.json unreadable for %s, treating as not-yet-succeeded: %s", sj, exc)
            return False
        if sr.state != StageState.SUCCEEDED:
            return False

        # Outputs must also exist — guards against an orphaned stage.json
        # whose declared outputs were deleted out from under us.
        if stage_def.exit_condition.required_outputs and self._missing_required_outputs(stage_def):
            return False
        validator_errors = self._run_output_validators(stage_def)
        if validator_errors:
            log.warning(
                "stage %s is SUCCEEDED but validators fail on resume; will re-run: %s",
                stage_def.id,
                validator_errors,
            )
            return False
        return True

    def _missing_required_outputs(self, stage_def: StageDefinition) -> list[str]:
        if not stage_def.exit_condition.required_outputs:
            return []
        job_dir = self._job_dir()
        return [
            ro.path
            for ro in stage_def.exit_condition.required_outputs
            if not (job_dir / ro.path).exists()
        ]

    def _run_output_validators(self, stage_def: StageDefinition) -> list[str]:
        """Run named validators on required_outputs and artifact_validators.

        Returns a list of error messages; empty means all pass.
        Fail-closed: unknown validator names are treated as errors so a
        misconfigured plan doesn't silently skip validation.
        """
        from shared.artifact_validators import REGISTRY

        job_dir = self._job_dir()
        errors: list[str] = []
        ec = stage_def.exit_condition
        for ro in ec.required_outputs or []:
            for name in ro.validators or []:
                fn = REGISTRY.get(name)
                if fn is None:
                    errors.append(f"{ro.path}: [{name}] unknown validator (not in registry)")
                    continue
                result = fn(job_dir / ro.path)
                if result is not None:
                    errors.append(f"{ro.path}: [{name}] {result}")
        for av in ec.artifact_validators or []:
            fn = REGISTRY.get(av.schema_)
            if fn is None:
                errors.append(f"{av.path}: [{av.schema_}] unknown validator (not in registry)")
                continue
            result = fn(job_dir / av.path)
            if result is not None:
                errors.append(f"{av.path}: [{av.schema_}] {result}")
        return errors

    def _missing_final_outputs(
        self,
        stages: list[StageDefinition],
        *,
        skipped: set[str] | None = None,
    ) -> list[str]:
        """Validate every stage's required_outputs are on disk before COMPLETED.

        Stage 12.5 (A6): exempts stages that were *actually skipped at
        dispatch time*, not stages whose predicate happens to be false now.
        ``skipped`` is the set of stage ids the dispatch loop skipped via
        ``runs_if`` (false or PredicateError).  Re-evaluating the predicate
        here would mis-classify a stage that ran successfully but whose
        predicate-referenced artifact was later deleted: the dispatch path
        ran the stage, so its outputs are required, but a re-evaluation of
        ``runs_if`` would now fail, silently exempting the missing outputs
        and letting the job COMPLETE with a real integrity violation.
        Tracking the dispatch decision is the correct gate.
        """
        skipped = skipped or set()
        missing: list[str] = []
        for s in stages:
            if s.id in skipped:
                continue
            missing.extend(self._missing_required_outputs(s))
        return missing

    def _inputs_ready(self, stage_def: StageDefinition) -> bool:
        job_dir = self._job_dir()
        return all((job_dir / inp).exists() for inp in stage_def.inputs.required)

    def _build_predicate_context(self) -> dict[str, Any]:
        """Build a context dict for predicate evaluation from job dir files."""
        job_dir = self._job_dir()
        ctx: dict[str, Any] = {}
        for f in job_dir.iterdir():
            if f.is_file() and f.suffix == ".json":
                try:
                    parsed = json.loads(f.read_text())
                    # e.g. "design-spec-review-agent.json" → stem="design-spec-review-agent"
                    # predicate "design-spec-review-agent.json.verdict" splits to
                    # ("design-spec-review-agent", "json", "verdict")
                    stem = f.stem
                    ext = f.suffix.lstrip(".")  # "json"
                    ctx.setdefault(stem, {})[ext] = parsed
                except (json.JSONDecodeError, OSError):
                    pass
        return ctx

    def _check_cancel_command(self) -> bool:
        """Return True if human-action.json contains a cancel command."""
        action_path = paths.job_human_action(self.job_slug, root=self.root)
        if not action_path.exists():
            return False
        try:
            payload = json.loads(action_path.read_text())
            return payload.get("command") == "cancel"
        except (json.JSONDecodeError, OSError):
            return False

    def _initial_seq(self) -> int:
        """Resume seq counter from existing events.jsonl (max+1).

        Per design § Recovery — append-only logs use monotonic sequence
        numbers. A restarted driver must not re-emit seq values already
        written to disk by a previous instance.

        Tolerates corrupt/truncated tails: malformed lines are skipped.
        """
        events_path = paths.job_events_jsonl(self.job_slug, root=self.root)
        if not events_path.exists():
            return 0
        max_seq = -1
        try:
            for line in events_path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue  # tolerate truncated tail
                seq = obj.get("seq")
                if isinstance(seq, int) and seq > max_seq:
                    max_seq = seq
        except OSError:
            return 0
        return max_seq + 1

    def _emit_event(
        self,
        event_type: str,
        stage_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        cfg = self._read_job_config()
        event = Event(
            seq=self._seq,
            timestamp=self._now(),
            event_type=event_type,
            source="job_driver",
            job_id=cfg.job_id,
            stage_id=stage_id,
            payload=payload or {},
        )
        self._seq += 1
        events_path = paths.job_events_jsonl(self.job_slug, root=self.root)
        atomic_append_jsonl(events_path, event)

    def _touch_heartbeat(self) -> None:
        hb_path = paths.job_heartbeat(self.job_slug, root=self.root)
        hb_path.parent.mkdir(parents=True, exist_ok=True)
        hb_path.touch()

    def _write_stage_run(self, stage_id: str, stage_run: StageRun) -> None:
        p = paths.stage_json(self.job_slug, stage_id, root=self.root)
        atomic_write_json(p, stage_run)

    def _next_attempt(self, stage_id: str) -> int:
        """Return the next attempt number (1-based) for this stage."""
        stages_dir = paths.stage_dir(self.job_slug, stage_id, root=self.root)
        if not stages_dir.exists():
            return 1
        existing = [
            int(d.name.split("-")[1])
            for d in stages_dir.iterdir()
            if d.is_dir() and d.name.startswith("run-") and d.name[4:].isdigit()
        ]
        return (max(existing) + 1) if existing else 1

    def _reset_stage_run(self, stage_def: StageDefinition) -> None:
        """Delete stage.json so a re-run is forced. Outputs are preserved."""
        sj = paths.stage_json(self.job_slug, stage_def.id, root=self.root)
        if sj.exists():
            sj.unlink()

    def _clear_stage_outputs(self, stage_defs: list[StageDefinition]) -> None:
        """Remove required output files for the given stages (to force re-run).

        Also resets each stage's stage.json so resume detection treats them
        as PENDING rather than skipping by stale SUCCEEDED state.
        """
        job_dir = self._job_dir()
        for stage_def in stage_defs:
            if stage_def.exit_condition.required_outputs:
                for ro in stage_def.exit_condition.required_outputs:
                    p = job_dir / ro.path
                    if p.exists():
                        p.unlink()
            # Reset stage.json so _stage_already_succeeded returns False.
            sj = paths.stage_json(self.job_slug, stage_def.id, root=self.root)
            if sj.exists():
                sj.unlink()

    def _update_latest_symlink(self, stage_id: str, target_name: str) -> None:
        """Atomically point the ``latest`` symlink at ``target_name``.

        Creates a uniquely named temp symlink and ``os.replace()``-es it
        over ``latest`` so concurrent readers never observe a missing link.
        """
        latest = paths.stage_run_latest(self.job_slug, stage_id, root=self.root)
        latest.parent.mkdir(parents=True, exist_ok=True)
        tmp = latest.parent / f".latest.tmp.{uuid.uuid4().hex}"
        tmp.symlink_to(target_name)
        os.replace(tmp, latest)

    def _block_on_human(self, stage_def: StageDefinition, *, reason: str) -> None:
        """Mark the active stage and the job as BLOCKED_ON_HUMAN, then exit.

        The driver returns control to its caller (typically ``main()``); the
        dashboard re-spawns the driver after the human action lands on disk.

        P5 (real-claude e2e precondition track): in addition to the
        stage.json + job.json transitions, create a HilItem so
        ``POST /api/hil/{id}/answer`` can resolve the freshly-blocked
        stage. Without this the operator (and the e2e test) had nothing
        to answer against.
        """
        # Persist stage as BLOCKED_ON_HUMAN (creates stage.json if absent).
        sj = paths.stage_json(self.job_slug, stage_def.id, root=self.root)
        attempt = self._next_attempt(stage_def.id) if not sj.exists() else 1
        try:
            existing = StageRun.model_validate_json(sj.read_text()) if sj.exists() else None
        except (json.JSONDecodeError, ValidationError, OSError) as exc:
            # Stage 12.5 (A7): narrow + log.  An unreadable existing stage.json
            # falls back to a fresh PENDING — same behaviour as before, but the
            # cause is now visible.
            log.warning("existing stage.json unreadable for %s, starting fresh: %s", sj, exc)
            existing = None
        stage_run = (
            existing
            or StageRun(
                stage_id=stage_def.id,
                attempt=attempt,
                state=StageState.PENDING,
                started_at=self._now(),
            )
        ).model_copy(update={"state": StageState.BLOCKED_ON_HUMAN})
        self._write_stage_run(stage_def.id, stage_run)
        self._emit_event(
            "stage_state_transition",
            stage_id=stage_def.id,
            payload={"to": "BLOCKED_ON_HUMAN", "reason": reason},
        )

        # P5: create + persist the HilItem so the answer endpoint can
        # resolve it. Best-effort with narrow except so a write failure
        # doesn't block the stage/job state transitions above.
        # Split try blocks (codex review on PR #28): a HilItem that
        # persisted but whose hil_item_opened event failed to emit is
        # NOT a "could not write HilItem" condition — the dashboard
        # watcher tailer will pick the item up off disk regardless.
        from shared.hil_factory import create_stage_block_hil_item

        item = None
        try:
            item = create_stage_block_hil_item(
                job_slug=self.job_slug,
                stage_id=stage_def.id,
                instructions=(
                    f"Stage {stage_def.id!r} is blocked on a human. "
                    f"Reason: {reason}. Resolve the gate (write the required "
                    f"output and/or answer this HIL item) so the driver can "
                    f"resume."
                ),
                root=self.root,
                now=self._now(),
            )
        except OSError as exc:
            log.warning(
                "could not write HilItem for stage block %s/%s: %s",
                self.job_slug,
                stage_def.id,
                exc,
            )
        if item is not None:
            try:
                self._emit_event(
                    "hil_item_opened",
                    stage_id=stage_def.id,
                    payload={
                        "item_id": item.id,
                        "stage_id": stage_def.id,
                        "kind": item.kind,
                    },
                )
            except OSError as exc:
                log.warning(
                    "HilItem %s persisted but hil_item_opened event emit failed: %s",
                    item.id,
                    exc,
                )

        self._write_job_state(JobState.BLOCKED_ON_HUMAN)
        log.info(
            "Job %s blocked on human (stage=%s, reason=%s)", self.job_slug, stage_def.id, reason
        )

    def _fail_stage(self, stage_def: StageDefinition, *, reason: str) -> None:
        """Persist a stage as FAILED with a reason, before failing the job."""
        sj = paths.stage_json(self.job_slug, stage_def.id, root=self.root)
        try:
            existing = StageRun.model_validate_json(sj.read_text()) if sj.exists() else None
        except (json.JSONDecodeError, ValidationError, OSError) as exc:
            # Stage 12.5 (A7): narrow + log.  Same treatment as
            # ``_block_on_human`` — fall back to fresh state, log the cause.
            log.warning("existing stage.json unreadable for %s, starting fresh: %s", sj, exc)
            existing = None
        attempt = existing.attempt if existing is not None else self._next_attempt(stage_def.id)
        stage_run = (
            existing
            or StageRun(
                stage_id=stage_def.id,
                attempt=attempt,
                state=StageState.PENDING,
                started_at=self._now(),
            )
        ).model_copy(update={"state": StageState.FAILED, "ended_at": self._now()})
        self._write_stage_run(stage_def.id, stage_run)
        self._emit_event(
            "stage_state_transition",
            stage_id=stage_def.id,
            payload={"to": "FAILED", "reason": reason},
        )
        # Codex review of PR #23: manifest is the integrity contract for
        # every closed stage attempt — including ones that closed via
        # exception or predicate failure routed through here. Best-
        # effort: if no run dir exists (e.g. predicate failure before
        # _run_single_stage created it), the helper logs and returns.
        self._write_manifest_for_latest_run(stage_def.id)

    def _write_manifest_for_latest_run(self, stage_id: str) -> None:
        """Write the integrity manifest for the latest run dir of *stage_id*.

        No-op (with a debug log) when the run dir doesn't exist yet —
        e.g. a stage that failed at the predicate stage before its run
        dir was created. Failure to write the manifest must never mask
        the stage outcome, so all errors are caught + logged.
        """
        latest = paths.stage_run_latest(self.job_slug, stage_id, root=self.root)
        if not latest.exists():
            log.debug("no stage run dir for %s — skipping manifest write", stage_id)
            return
        try:
            from job_driver.archive import write_manifest

            write_manifest(latest)
        except Exception as exc:
            log.warning(
                "failed to write archive manifest for %s/%s: %s",
                self.job_slug,
                stage_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Stage isolation (v0 alignment Plan #2 + #8)
    # ------------------------------------------------------------------

    def _project_repo(self) -> Path | None:
        """Resolve the project's repo path from job.json → project.json.

        Returns ``None`` if the project file is missing/unreadable or
        the repo isn't a git repo (fake-fixture / synthetic test
        fixtures). Callers treat ``None`` as "skip isolation".
        """
        try:
            from shared.models import ProjectConfig

            cfg = self._read_job_config()
            project_path = paths.project_json(cfg.project_slug, root=self.root)
            if not project_path.exists():
                return None
            project = ProjectConfig.model_validate_json(project_path.read_text())
            repo = Path(project.repo_path)
        except (FileNotFoundError, ValidationError, json.JSONDecodeError, OSError) as exc:
            log.warning("could not resolve project repo for %s: %s", self.job_slug, exc)
            return None
        if not (repo / ".git").exists():
            return None
        return repo

    def _stage_worktree_path(self, stage_id: str) -> Path:
        """Per-stage worktree path under the job dir."""
        return paths.stage_dir(self.job_slug, stage_id, root=self.root) / "worktree"

    def _setup_stage_isolation(self, stage_id: str) -> None:
        """Create the stage branch + worktree before the runner starts.

        Idempotent: a worktree that already exists for the same branch
        (e.g. resume after crash) is reused.

        Failure handling — Codex review of PR #24 narrowed the catch
        from a blanket ``Exception`` to a specific list because the
        original posture silently masked permission errors, disk-full,
        and (most dangerously) ``WorktreeExistsError`` for *branch
        mismatches* (which would have left a stale dir for the wrong
        branch and silently run claude in it):

        - ``BranchNotFoundError`` (parent branch missing — synthetic
          test repos) → log + skip isolation.
        - ``subprocess.CalledProcessError`` (low-level git failures
          like a partially-initialised repo) → log + skip.
        - ``WorktreeExistsError`` for a *branch mismatch* → **fatal**:
          re-raise so the JobDriver fails the stage rather than running
          in a stale worktree.
        - Anything else (PermissionError, OSError, etc.) → re-raise.
        """
        repo = self._project_repo()
        if repo is None:
            return
        from dashboard.code.branches import BranchNotFoundError, create_stage_branch
        from dashboard.code.worktrees import WorktreeExistsError, add_worktree

        try:
            branch = create_stage_branch(repo, self.job_slug, stage_id)
        except BranchNotFoundError as exc:
            log.warning(
                "stage isolation skipped for %s/%s: parent branch missing (%s)",
                self.job_slug,
                stage_id,
                exc,
            )
            return
        except subprocess.CalledProcessError as exc:
            log.warning(
                "stage isolation skipped for %s/%s: git error creating stage branch (%s)",
                self.job_slug,
                stage_id,
                exc,
            )
            return

        wt = self._stage_worktree_path(stage_id)
        try:
            add_worktree(repo, wt, branch, reuse_existing=True)
        except WorktreeExistsError:
            # Branch mismatch — stale worktree for a different branch.
            # Re-raise so the JobDriver fails the stage loudly. Silent
            # fall-through here would run claude in the wrong branch.
            raise
        except subprocess.CalledProcessError as exc:
            log.warning(
                "stage isolation skipped for %s/%s: git error adding worktree (%s)",
                self.job_slug,
                stage_id,
                exc,
            )
            return
        # P4 (real-claude e2e precondition track): visibility for the
        # worktree lifecycle. Without this, "did we get isolation for
        # this stage?" is only answerable by `git worktree list` from
        # outside the system.
        self._emit_event(
            "worktree_created",
            stage_id=stage_id,
            payload={
                "stage_id": stage_id,
                "path": str(wt),
                "branch": branch,
            },
        )

    # NOTE on v0 worktree teardown (Codex review of PR #24, HIGH 1):
    #
    # Earlier drafts of this PR removed the stage worktree at every
    # terminal stage state (success, failure, cancel, wall-clock,
    # exception). Per `docs/design.md` § Code plane the workspace is
    # supposed to survive until the stage's PR is merged so an
    # operator can inspect the agent's state, fix it up, or re-run
    # the stage from the same checkout. v0 doesn't have PR-merge
    # tracking, so we keep ALL worktrees post-stage and defer the
    # automatic cleanup pass to a v1+ task documented in
    # `docs/implementation.md § 9`. Operators clean up manually with
    # `git worktree remove <path>` + `git worktree prune`.

    # ------------------------------------------------------------------
    # Specialist catalogue (v0 alignment Plan #3)
    # ------------------------------------------------------------------

    def _materialise_specialists_for_stage(self, stage_run_dir: Path) -> None:
        """Write ``<stage_run_dir>/agents.json`` from the project's
        specialist catalogue so RealStageRunner can pass it to claude
        via ``--agents <inline json>``.

        ``resolve`` already logs+skips per-file malformed overrides;
        the catch here narrows to filesystem / model-parse errors so
        a missing ``project.json`` or atomic_write failure doesn't
        crash the driver — programming bugs still propagate.
        """
        try:
            from dashboard.specialist.materialise import materialise_for_spawn
            from shared.models import ProjectConfig

            cfg = self._read_job_config()
            project_path = paths.project_json(cfg.project_slug, root=self.root)
            if not project_path.exists():
                return
            project = ProjectConfig.model_validate_json(project_path.read_text())
            materialise_for_spawn(project, stage_run_dir)
        except (OSError, ValueError) as exc:
            log.warning(
                "could not materialise specialist catalogue for %s: %s",
                self.job_slug,
                exc,
            )
