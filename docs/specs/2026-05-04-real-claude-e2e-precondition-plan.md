# Real-Claude E2E Precondition Track — Implementation Plan

**Status:** proposed
**Date:** 2026-05-04
**Source design:** `docs/specs/2026-05-03-real-claude-e2e-test-design.md`

This plan implements the five precondition PRs (P1–P5) named in the source design. The e2e test PR itself is **not** in scope here — it ships as the closing PR after all five preconditions land.

---

## Validation of source-doc findings

All five claims in the source design were spot-checked against current `main`. Each is correct:

| # | Claim | Evidence | Status |
|---|---|---|---|
| P1 | `_build_runner` ignores 4 of 6 RealStageRunner kwargs | `job_driver/__main__.py:85` passes only `project_root` + `claude_binary`; `RealStageRunner.__init__` accepts `mcp_manager`, `stop_hook_path`, `job_slug`, `hammock_root` | ✓ |
| P2 | RealStageRunner sends `stage_def.description or stage_def.id` as the prompt | `job_driver/stage_runner.py:266`/269 builds `-p <prompt>` from that one field; no job context | ✓ |
| P3 | `JobDriver` reads `stage-list.yaml` once at start; never re-reads after expander stages | `job_driver/runner.py:164` calls `_read_stages()` once in `_execute_stages`; `is_expander: bool = False` exists at `shared/models/stage.py:151` but no re-read logic | ✓ |
| P4 | No `worktree_created`/`worktree_destroyed`/`worker_exit` event types | `shared/models/events.py` `EVENT_TYPES` frozenset (lines 27–57) contains none of them; `grep -r` finds no production emissions | ✓ |
| P5 | `_block_on_human` writes only `stage.json` + `job.json`; HilItem absent | `job_driver/runner.py:845–866` writes neither HilItem nor the cache record; `dashboard/api/hil.py::submit_answer` 404s on `cache.get_hil(item_id)` miss; HilItems are only created by `dashboard/mcp/server.py:240::open_ask` (MCP tool) | ✓ |

The track is correctly scoped. Plan below treats each P as one PR.

---

## Sequencing + parallelism

```
P5 ──┐
P4 ──┼─→ (independent of each other and of P1/P2/P3)
P1 ──┘
                    ┌── P2 (depends on P1: prompt builder needs the wiring point)
                    └── P3 (independent of P2 logically, but easier to test after P2 because expanders produce stage outputs that prompts depend on)

E2E PR — depends on P1+P2+P3+P4+P5
```

Tracks that can run in parallel: **{P1+P2+P3 chain}** and **P4** and **P5**. Three reviewable streams maximum.

---

## P1 — Runner plumbing

**Goal.** `_build_runner` constructs the four wired-up dependencies and threads them through.

**Files touched**
- `job_driver/__main__.py` — extend `_build_runner` to accept `job_slug`, `hammock_root`, build `MCPManager`, resolve Stop-hook script path, pass all four.
- `tests/job_driver/test_main_runner_selection.py` — extend the existing test to assert all four kwargs are forwarded.
- `tests/job_driver/test_main_real_runner_wiring.py` — **new** — focused test that constructs the runner via `_build_runner` and asserts it is configured with the expected MCPManager + stop_hook_path.

**TDD steps**
1. RED — add a test asserting `_build_runner(...)` returns a runner whose `_mcp_manager` is an `MCPManager` instance with `_default_root` matching the job's `hammock_root`, and whose `_stop_hook_path` matches the bundled script.
2. RED — second test: `_job_slug` and `_hammock_root` set on the runner.
3. GREEN — change `_build_runner` to accept `job_slug` + `hammock_root`, build `MCPManager()`, resolve the Stop-hook script path (look up where `hammock/scripts/stop_hook.py` or equivalent lives — confirm during implementation), pass all four.
4. GREEN — update the call site in `main()` so `job_slug` and `hammock_root` flow through (they're parsed from CLI args already).

**Risks**
- The Stop-hook script path is currently un-bundled. If it doesn't exist as a real script today, P1 must include creating it; flag during implementation.
- `MCPManager()` no-arg construction defaults to `default_python_executable` / `dashboard.mcp` module. Confirm those defaults are right for production; if not, surface as a separate finding.

**Done when**
- `_build_runner` passes 6/6 kwargs.
- Two new tests + extended existing test all pass.
- `uv run pytest tests/job_driver/test_main_*` green.
- No regression in fake-mode runner construction (the fake-fixtures path must continue to ignore the new kwargs).

---

## P2 — Real-mode prompt construction

**Goal.** Replace the one-line prompt with a structured, job-context-aware prompt the agent can actually act on.

**Files touched**
- `job_driver/stage_runner.py` — replace `prompt = stage_def.description or stage_def.id` with a call to a new `build_stage_prompt(...)` helper.
- `job_driver/prompt_builder.py` — **new module** — pure function `build_stage_prompt(stage_def, job_config, job_dir, project, stage_run_dir) -> str`. Reads `prompt.md`, resolves stage `inputs` against the job dir + previous stage outputs, lists declared `required_outputs`, names the branch + worktree the stage owns.
- `tests/job_driver/test_prompt_builder.py` — **new** — table-driven tests covering: no inputs, one input, multiple inputs, missing-but-optional input, declared outputs section, branch/worktree section, a snapshot test for the rendered prompt of a known stage definition.
- `tests/job_driver/test_real_stage_runner.py` — extend to assert the prompt fed to `claude -p` includes the job prompt + stage outputs section (mock the subprocess so we can capture argv).

**TDD steps**
1. RED — write `test_prompt_builder.py` covering the prompt sections. All fail because the module doesn't exist.
2. GREEN — implement `prompt_builder.build_stage_prompt`. Sections, in order: stage description; the job's overall prompt; resolved inputs (file path + content excerpt); declared outputs the stage MUST produce, with the schema name beside each path; the branch + worktree the stage owns.
3. RED — add the assertion in `test_real_stage_runner.py` that the prompt argv contains a known sentinel from `prompt_builder` (e.g. `"## Required outputs"`).
4. GREEN — wire `build_stage_prompt` into `RealStageRunner.run` (pass it `job_config`, `project`, etc. — `RealStageRunner` already has `project_root` and now `job_slug` + `hammock_root` from P1; missing pieces come via the `run()` arguments which already include `stage_def`/`job_dir`/`stage_run_dir`).

**Risks**
- Token bloat. A long job prompt + multiple input excerpts could blow past Claude context budgets. Mitigation: cap each input excerpt at a fixed byte count (e.g. 16 KB) with a clear "[truncated]" marker; full file is always available on disk via the path we name.
- Inputs that don't yet exist (referenced by an upstream stage that ran but failed to produce them). Builder must surface this as part of the prompt — "expected input X not found" — rather than silently dropping; the agent can then choose to fail loudly. Stop-hook validation will catch it regardless.
- Branch/worktree information depends on PR2 (stage isolation) which already shipped. Confirm the names available on `JobConfig` / project state are stable.

**Done when**
- `build_stage_prompt` is a pure function with full unit-test coverage.
- `RealStageRunner` invokes it and the `claude -p` argv includes the structured prompt.
- A snapshot test pins the output for a representative stage so future regressions show as a focused diff.

---

## P3 — Dynamic stage handling

**Goal.** After any stage with `is_expander: true` succeeds, the driver re-reads `stage-list.yaml` so the appended stages are picked up.

**Files touched**
- `job_driver/runner.py` — change `_execute_stages` from a single `_read_stages()` + linear iteration to a loop that:
  1. Reads the current stage list.
  2. Picks the next stage that's still `PENDING` per `stage.json` (skipping `SUCCEEDED`/`SKIPPED`).
  3. Runs it.
  4. If the stage's `StageDefinition.is_expander` is true and the stage succeeded, re-reads `stage-list.yaml` before stepping forward.
  5. Exits when no `PENDING` stage remains.
- `tests/job_driver/test_runner_dynamic_expansion.py` — **new** — uses fake fixtures: an expander stage that, when run, appends two stages to the YAML on disk; assert the appended stages execute.
- `docs/design.md` — small note describing the contract: an expander stage is responsible for atomically rewriting `stage-list.yaml` before it returns; the driver is responsible for picking up the new tail.

**TDD steps**
1. RED — write the test with a fake fixture that mimics an expander rewriting the stage list. The test currently fails because the appended stages are never executed.
2. GREEN — refactor `_execute_stages` per above.
3. Add a regression test: non-expander stage that mutates `stage-list.yaml` is **not** picked up (expansion is gated on `is_expander: true` AND `SUCCEEDED`).

**Risks**
- Race: an expander rewrites the file while the driver is mid-read. Mitigation: expanders use the same `atomic_write_text` shared util; driver uses one read per iteration so torn reads are impossible.
- A bad expander that rewrites the YAML to remove already-completed stages. Mitigation: the loop trusts `stage.json` per-stage state for "is this done?", not the YAML order, so removed entries are inert; new entries get picked up.
- Infinite expansion (expander appends another expander that appends another...). Mitigation: bound the loop at a generous safety cap (e.g. 1000 stages per job) and surface as a hard error with the stage list dumped.

**Done when**
- Expander stages append to the YAML and the appended stages run to completion.
- Non-expander stages cannot inject new stages (test proves this).
- All existing `test_runner.py` tests still pass.

---

## P4 — Event-taxonomy additions

**Goal.** Add `worktree_created`, `worktree_destroyed`, and `worker_exit` to the event taxonomy and emit them.

**Files touched**
- `shared/models/events.py` — add the three new types to `EVENT_TYPES` and any related literals.
- `tests/shared/test_event_types.py` — **extend** (or new) — assert the three are present.
- `dashboard/git/worktrees.py` (created in PR2) — emit `worktree_created` on creation and `worktree_destroyed` on cleanup. Sink: `events.jsonl` via the existing event writer.
- `job_driver/stage_runner.py` — emit `worker_exit` with `{exit_code, signal, stage_id}` after the claude subprocess returns (both success and failure paths).
- `tests/dashboard/git/test_worktrees_events.py` — **new** — assert event emission for create + destroy.
- `tests/job_driver/test_real_stage_runner_events.py` — **new** — assert `worker_exit` event with the right exit code on subprocess completion (mock subprocess).

**TDD steps**
1. RED — add the event-types test with the three new strings; fail.
2. GREEN — extend the frozenset.
3. RED — write the worktrees create/destroy event-emission tests; fail because emission doesn't exist.
4. GREEN — wire emission via the existing event writer in worktrees module. Find the writer pattern by inspecting how, e.g., `stage_started` is emitted today — reuse the same helper.
5. RED — write the `worker_exit` test with a mock subprocess returning code 0 and code 1; fail.
6. GREEN — wire emission at the end of `RealStageRunner.run` and on every error/cancellation cleanup path. Make sure cancellation also emits with a sentinel exit code (e.g. `-signal.SIGTERM`).

**Risks**
- Emission must survive process death. `worker_exit` is the most likely to be lost if the driver itself dies. v0 acceptance: emit only on the subprocess-return path; if the driver dies before that, `events.jsonl` won't have the event and the e2e test will surface the regression — that's correct behaviour.
- Schema compatibility: add the new types only as `Literal` additions; do not change the existing event payload shapes. Pydantic validators stay backward compatible.

**Done when**
- Three new event types in the taxonomy.
- Worktree creation/destruction emits events.
- Every claude subprocess invocation under `RealStageRunner` emits `worker_exit`.
- `tests/shared/test_event_types.py` plus the two new test modules all pass.

---

## P5 — HilItem creation on human-stage block

**Goal.** When a stage transitions to `BLOCKED_ON_HUMAN`, also create the corresponding `HilItem` and persist it so `POST /api/hil/{id}/answer` resolves cleanly instead of 404.

**Files touched**
- `job_driver/runner.py` `_block_on_human` — after the existing stage.json + job.json writes, construct a `HilItem` (kind derived from the stage's HIL template, payload from the stage's declared inputs/schema), write it via the same atomic-write path used by `dashboard/mcp/server.py::open_ask`, and add it to the cache through the existing append path.
- `shared/models/hil.py` (or wherever HilItem lives) — confirm `HilItem.from_stage(...)` constructor exists; if not, add it.
- `tests/job_driver/test_runner_block_on_human.py` — **new** — drive a fake stage to block and assert `paths.hil_item(...)` exists with the expected fields.
- `tests/dashboard/api/test_hil_post.py` — extend to cover the "stage block creates HilItem; POST /answer succeeds" loop end-to-end against an in-process app.

**TDD steps**
1. RED — write `test_runner_block_on_human.py` asserting the HilItem file exists after `_block_on_human` runs. Fails today.
2. GREEN — implement HilItem creation in `_block_on_human`. Reuse `open_ask`'s persistence path so there's exactly one writer.
3. RED — extend `test_hil_post.py` with the integration scenario.
4. GREEN — confirm the answer endpoint resolves; if it doesn't, the cache append needs to be wired here too.

**Risks**
- Two writers (`_block_on_human` and `open_ask`) for the same record type. Mitigation: factor a single `create_hil_item(...)` helper that both call. Code review surface stays small.
- Stage-block payload schema differs from MCP-tool-block payload schema. Mitigation: make the HIL kind explicit on the HilItem (`stage_block` vs `agent_ask`); endpoints already key off kind.

**Done when**
- `_block_on_human` creates the HilItem.
- The POST `/api/hil/{id}/answer` endpoint resolves the freshly-created item without 404.
- Existing `open_ask` MCP tool still works (no regression).

---

## Cross-cutting concerns

**Test conventions.** Every PR follows the existing TDD pattern: RED → GREEN → REFACTOR with a `verify_fail_first` check before claiming any green. New tests live alongside existing ones in `tests/`.

**Verify suite per PR.** `uv run ruff check . && uv run ruff format . && uv run pyright && uv run pytest tests/` plus the frontend suite if that PR touches the dashboard frontend (none of P1–P5 do).

**Codex review per PR.** Same pattern as the PR3 work — `Agent(subagent_type="codex:codex-rescue")` after the local verify suite is green; address findings in a follow-up commit before merge.

**Commit shape.** One commit per PR's `Files touched` group, with the commit message naming the precondition number (e.g. `feat(driver): P1 — wire MCPManager + Stop-hook into RealStageRunner`).

**No e2e test in this track.** The closing PR (the e2e test itself) ships separately and is gated on all five preconditions merging to `main`.

---

## Open questions for the user

1. **Stop-hook script existence.** P1 assumes a Stop-hook script is bundled with Hammock today. If it isn't, P1's scope grows to include creating + bundling it. Worth a quick `find` before starting P1.
2. **Order preference.** Three independent streams — pick one to start? Default suggestion: P1 first (smallest, unblocks P2), then P2 + P4 + P5 in parallel (three small reviewable PRs), then P3.
3. **Codex review per-PR vs only on the chain.** Each precondition is small; per-PR review will catch issues earlier but adds overhead. Per-chain review is cheaper but defers integration risk. Default: per-PR.
