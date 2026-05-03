`JobDriver` is a subprocess-based deterministic state machine that executes a compiled job's stage list. It runs as a long-lived daemon (`python -m job_driver <slug> [--root <path>] [--fake-fixtures <dir>]`), survives dashboard restarts, and uses `FakeStageRunner` as the stub for real Claude Code sessions (Stage 5).

- **`job_driver/runner.py`**. Core state machine. `JobDriver.run()` installs a SIGTERM handler, touches the heartbeat, starts background heartbeat/poll tasks, then drives `_execute_stages()`. For each stage it:
  1. Checks `_cancel_event` at the top of the loop.
  2. Evaluates `runs_if` predicate; skips if false.
  3. Checks whether all required outputs already exist (resume after crash).
  4. Calls `_run_single_stage()`, which **races** `stage_task` against `cancel_task` using `asyncio.wait(return_when=FIRST_COMPLETED)`. Returns `None` if cancellation wins.
  5. Handles `loop_back`: if the condition holds and the iteration counter is within `max_iterations`, clears the target-range outputs and re-enters at the target index.
  6. On completion writes COMPLETED / FAILED / ABANDONED as appropriate.
  Heartbeat is touched at startup AND at the start of each `_heartbeat_loop()` iteration (touch-before-sleep ensures the file exists immediately).

- **`job_driver/stage_runner.py`**. `StageRunner` Protocol (`async def run(stage_def, job_dir, stage_run_dir) -> StageResult`) and `FakeStageRunner`. The fake reads `<fixtures_dir>/<stage_id>.yaml`:
  ```yaml
  outcome: succeeded   # or "failed"
  delay_seconds: 0.1
  cost_usd: 0.01
  artifacts:
    spec.md: "# Spec content"
    review.json: '{"verdict": "approved"}'
  reason: optional failure message
  ```
  Missing fixture → succeeds with no outputs (safe default, lets unlisted stages pass).

- **`job_driver/__main__.py`**. Entry point for `python -m job_driver`. Parses `<slug> [--root <path>] [--fake-fixtures <dir>]`, instantiates `FakeStageRunner` when `--fake-fixtures` is set, and calls `asyncio.run(driver.run())`.

- **`dashboard/driver/supervisor.py`**. `Supervisor` with configurable `heartbeat_interval` and `stale_factor`. `is_stale(heartbeat_path)` returns True if the file is absent or older than `heartbeat_interval × stale_factor` seconds. `get_pid(pid_path)` reads and returns the integer PID (None on any error).

- **`dashboard/driver/lifecycle.py`**. `spawn_driver()` uses `subprocess.Popen` (fire-and-forget) with `proc.returncode = 0` to suppress `ResourceWarning` from `Popen.__del__`. Writes the PID to `jobs/<slug>/job-driver.pid` atomically and returns the PID. The subprocess survives dashboard restarts because there is no transport attached.

- **`dashboard/driver/ipc.py`**. `write_cancel_command(job_slug, reason)` writes `{"command": "cancel", "reason": ...}` to `human-action.json`. `send_sigterm(pid)` calls `os.kill(pid, SIGTERM)`. `cancel_job()` does both: writes the command file, reads the PID file, sends SIGTERM, polls until the process exits or the timeout elapses.


1. **`asyncio.wait` for cancellation racing.** The naive approach (`_cancel_event.is_set()` only at loop top) fails to interrupt a long-running stage. Racing two tasks — the stage task and `cancel_event.wait()` — ensures cancellation latency equals at most `COMMAND_POLL_INTERVAL` (2 s) regardless of stage duration. Cancelling the losing task (`task.cancel() + gather(return_exceptions=True)`) avoids resource leaks.
2. **`subprocess.Popen` instead of `asyncio.create_subprocess_exec`.** The asyncio variant attaches a transport. When the dashboard code goes out of scope without awaiting the process, Python emits `ResourceWarning: subprocess still running` — which triggers `filterwarnings = ["error"]` in pytest and fails tests. `subprocess.Popen` with `proc.returncode = 0` suppresses `Popen.__del__`'s warning and leaves the subprocess fully detached.
3. **Heartbeat touches at startup in `run()`, not only inside `_heartbeat_loop()`.** The heartbeat loop sleeps first. Without an immediate touch, a fast-finishing job would never write the heartbeat file; `Supervisor.is_stale()` would return True immediately after spawn. The `run()` call touches it synchronously before the background tasks start.
4. **Predicate context keys match the YAML dotted path convention.** A file `design-spec-review-agent.json` → `ctx["design-spec-review-agent"]["json"] = {parsed}`. The predicate `design-spec-review-agent.json.verdict` resolves via `("design-spec-review-agent", "json", "verdict")`. Nesting under `"json"` preserves the extension as a path segment, matching the design doc's dotted-path syntax.
5. **Resume skips stages with all required outputs present.** `_stage_already_succeeded()` checks `exit_condition.required_outputs` paths exist in `job_dir`. If `required_outputs` is empty, it falls back to checking `stage.json` for `SUCCEEDED`. This lets a crashed driver re-attach and continue from the first incomplete stage without re-running successful ones.
6. **`loop_back` clears outputs for the re-run range.** Before jumping back to `target_idx`, `_clear_stage_outputs(stages[target_idx:i+1])` removes the required output files for all stages in the range, so the resume-skip check doesn't incorrectly skip them on the next pass.
7. **`send_sigterm(os.getpid())` is unsafe in tests.** Sending a real SIGTERM to the test process with asyncio's signal handler infrastructure active causes unpredictable termination. Tests use `monkeypatch.setattr(os, "kill", fake_kill)` to intercept the call without delivering an actual signal.


- **`spawn_driver` in `lifecycle.py` is the spawn contract.** Stage 14 (HTTP POST /api/jobs) calls it after `compile_job` returns `CompileSuccess`. The signature is `spawn_driver(job_slug, *, root, fake_fixtures_dir, python) -> int`. Don't change without a structural-change stage.
- **`FakeStageRunner` fixture format is versioned by this stage.** Stage 5 replaces `FakeStageRunner` with `RealStageRunner` (Claude Code subprocess), but `FakeStageRunner` is retained for all test suites. The YAML schema (outcome/delay_seconds/cost_usd/artifacts/reason) is the fixture contract; new fields must be backward-compatible.
- **`COMMAND_POLL_INTERVAL = 2.0 s` is the cancel latency floor.** Human-action cancellation is limited to ~2 s response time. SIGTERM delivers faster but requires the PID file to exist. For production, both paths run in parallel (`cancel_job()` does both). Future stages may want a dedicated cancel API endpoint that calls `cancel_job()`.
- **`Supervisor.is_stale()` uses wall-clock `time.time()`.** This is correct for heartbeat checks. If the system clock jumps backward (NTP adjustment), heartbeats may appear non-stale indefinitely. Acceptable for v0; a monotonic-clock version is a later hardening concern.
- **`loop_back.on_exhaustion` (HIL manual step) is a no-op in Stage 4.** The spec calls for escalating to a human-in-the-loop step when `max_iterations` is exhausted. Stage 7 delivers HIL. For now, exhaustion logs a warning and continues to the next stage.


```
job_driver/__init__.py                              (empty package marker)
job_driver/__main__.py                              (CLI entry point)
job_driver/runner.py                                (JobDriver state machine)
job_driver/stage_runner.py                          (StageRunner protocol + FakeStageRunner)

dashboard/driver/__init__.py
dashboard/driver/ipc.py                             (cancel helpers)
dashboard/driver/lifecycle.py                       (spawn_driver)
dashboard/driver/supervisor.py                      (heartbeat stale check)

tests/job_driver/__init__.py
tests/job_driver/test_runner.py                     (10 tests)
tests/job_driver/test_stage_runner.py               (5 tests)
tests/dashboard/driver/__init__.py
tests/dashboard/driver/test_ipc.py                  (5 tests)
tests/dashboard/driver/test_lifecycle.py            (3 tests)
tests/dashboard/driver/test_supervisor.py           (8 tests)

scripts/manual-smoke-stage04.py

pyproject.toml                                      (job_driver added to packages + pyright include)

docs/stages/stage-04.md                             (this file)
docs/stages/README.md                               (index updated)
```


None — all Stage 4 code is pure Python stdlib + existing project deps.


- [x] `JobDriver` transitions SUBMITTED → STAGES_RUNNING → COMPLETED on success
- [x] Stage failure → FAILED; cancel (command file + SIGTERM) → ABANDONED + active stage CANCELLED
- [x] `runs_if` predicate skips stages correctly
- [x] `loop_back` re-enters target stage bounded by `max_iterations`
- [x] Resume after crash: stages with existing outputs are skipped
- [x] Heartbeat file written at startup and every 30 s
- [x] `Supervisor.is_stale()` uses `mtime` age against configurable threshold
- [x] `spawn_driver` uses `subprocess.Popen` for fire-and-forget; PID written atomically
- [x] All 354 tests pass; ruff + pyright clean


- **Stage 5 (RealStageRunner)**: replace `FakeStageRunner` with a `RealStageRunner` that spawns a Claude Code subprocess per stage. The `StageRunner` Protocol is the stable interface; `FakeStageRunner` tests remain as regression guards.
- **Stage 7 (HIL)**: implement `loop_back.on_exhaustion` escalation — pause the job and write a human-action request instead of continuing silently.
- **Stage 14 (HTTP API)**: wire `POST /api/jobs` to call `compile_job` then `spawn_driver`. The `spawn_driver` call is the connection point between the two stages.
- **Stage 6 (events + SSE)**: the `events.jsonl` writer is already active from Stage 4. Stage 6 adds the SSE read-tail endpoint that streams these events to the UI.
- **Stale-driver recovery**: `Supervisor.is_stale()` already detects dead drivers. Stage 10 (or a dedicated recovery stage) should wire the supervisor check to a restart or FAILED escalation policy.
