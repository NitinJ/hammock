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

        # Race the stage runner against the cancel event
        stage_task = asyncio.create_task(self._stage_runner.run(stage_def, job_dir, stage_run_dir))
        cancel_task = asyncio.create_task(self._cancel_event.wait())

        done, pending = await asyncio.wait(
            {stage_task, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

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
        result: StageResult = stage_task.result()

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
        self._emit_event(
            "stage_state_transition",
            stage_id=stage_def.id,
            payload={"from": "RUNNING", "to": final_state, "cost_usd": result.cost_usd},
        )

        if result.cost_usd > 0:
            # Per design doc § Observability § Event taxonomy: cost_accrued
            # payload uses ``delta_usd`` / ``delta_tokens`` / ``running_total``.
            # v0 driver only knows the per-stage USD delta; tokens and the
            # accumulator are reserved for v1+.  Stage 12.5 (A3) aligned this
            # key with both the spec and the projection reader, which had
            # silently been folding to 0 because driver, projection, and spec
            # all named the field differently.
            self._emit_event(
                "cost_accrued",
                stage_id=stage_def.id,
                payload={"delta_usd": result.cost_usd},
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

    def _read_stages(self) -> list[StageDefinition]:
        stage_list_path = paths.job_stage_list(self.job_slug, root=self.root)
        data = yaml.safe_load(stage_list_path.read_text()) or {}
        return [StageDefinition.model_validate(s) for s in data.get("stages", [])]

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
        if stage_def.exit_condition.required_outputs:
            return not self._missing_required_outputs(stage_def)
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
