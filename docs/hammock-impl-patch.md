# Hammock implementation patch

The execution plan for the design captured in `docs/hammock-design-patch.md`. Where the design patch says *what* to change, this doc says *how* to land it: what to throw away, what to keep, and the test-first iteration strategy.

## Guiding principles

1. **Test first, at every level.** Two layers, both written before the code they cover:
   - **Unit tests** for each module — written first, drive the module's behaviour.
   - **End-to-end test** for the system — written first, asserts the whole flow.
   The e2e test is the system's spec; unit tests are each module's spec. Don't rush to the e2e green light by skipping unit coverage; bugs caught at the module level are an order of magnitude cheaper to fix.
2. **Iterate on YAML complexity, not code structure.** Once unit tests pass for a module, run the e2e against a tiny 2-node workflow. Fix bugs. Add a node, re-run. Repeat until the full fix-bug workflow runs. Each YAML stage isolates a smaller surface, so bugs are easier to localise.
3. **Throw away aggressively.** The dogfood proved that v0's stage-execution machinery is the wrong shape. We rewrite the parts that hold the old shape; we keep only the parts that are still right.
4. **Real Claude + real GitHub from day one.** Fakes hide bugs that the dogfood surfaced. The e2e test runs against the actual APIs.

### Test discipline within each step

For every module added in §5 (Order of work):

1. Write the unit tests for the module's public surface.
2. Implement the module until unit tests pass.
3. Run the broader unit-test suite to catch regressions.
4. Only then advance to the next module or the e2e step.

The e2e test exists from day one but only goes green once the module set for that stage is complete and unit-tested. Unit tests stay green forever — a regression in any unit test blocks further work until fixed.

---

## 1. What to throw away

Hammock v0 has a working pipeline, but the parts that encode the old contract are not salvageable. These get rewritten from scratch:

| Module | Why it goes |
|---|---|
| `job_driver/runner.py` (stage executor) | Stage-list iteration, naming-convention heuristics, plan.yaml merge, silent fall-through paths. The wrong execution model end-to-end. |
| `job_driver/prompt_builder.py` | `_SCHEMA_HINTS` dict, PR-protocol injection, JOB_DIR-vs-cwd boilerplate. Replaced by per-type `render_for_producer` / `render_for_consumer`. |
| `job_driver/stage_runner.py` | Wraps `claude -p` in a way coupled to the stage model. Replaced by a node dispatcher that knows kinds. |
| `shared/models/stage.py` (`StageDefinition`, `RequiredOutput`, `ArtifactValidator`, `ExitCondition`, etc.) | The stage shape is the wrong shape. Replaced by a discriminated union of `ArtifactNode | CodeNode | LoopNode`. |
| `shared/artifact_validators.py` | Generic schema-name registry. Replaced by per-type `Decl`/`Value` Pydantic models owned by each `VariableType`. |
| `shared/predicate.py` | `runs_if` predicate parser; needs to be rewritten for the new variable addressing scheme (`$loop-id.var[i].field`). |
| Plan.yaml merge logic in the driver | Dynamic stage-list mutation. Deleted entirely; replaced by static DAG + loop primitive. |
| HIL persistence path (cache._scan dependency) | Cache as gatekeeper. Replaced by disk-first reads. |
| Cleanup helpers that depend on cwd-relative `git push --delete` | Replaced by typed `branch` records carrying repo identity. |
| Outcome assertions in `tests/e2e/outcomes.py` | Stage-shaped assertions. Rewritten as variable-shaped assertions. |

## 2. What to keep / adapt

These keep their bones; some need refactor for the new model but the work is mechanical:

| Module | Disposition |
|---|---|
| `cli/` scaffolding (the `hammock` command structure) | Keep. New subcommands added: `hammock workflow validate <path>`. Existing `project register` / `job submit` adapt to new validation gates. |
| `shared/atomic.py` (atomic_write_text, atomic_append_jsonl) | Keep as-is. Used by the new persistence layer. |
| `shared/paths.py` | Keep but extend: new layout for typed variables, loop iterations, pending markers. |
| `dashboard/` (UI shell, listing endpoints) | Keep frontend. Listing endpoints adapt to read disk-first. New form rendering driven by variable type's `form_schema`. |
| `dashboard/code/branches.py` (`create_job_branch`, `create_stage_branch`, `delete_branch`) | Keep the primitives. Wire into the new substrate allocator (which owns when these run). |
| `dashboard/code/worktrees.py` (`add_worktree`, etc.) | Keep the primitives. Substrate allocator owns when. |
| `dashboard/mcp/` (MCP server framework) | Keep. Implicit HIL goes through it; needs a new `ask_human` tool that creates a typed pending item. |
| `shared/models/job.py` (JobConfig, JobState) | Keep. State machine adapts: long-lived driver, no exit on BLOCKED_ON_HUMAN. |
| `shared/models/events.py` | Keep events.jsonl shape. New event types added for variable lifecycle, loop iteration boundaries. |
| Cost tracking | Keep. |
| Heartbeat | Keep. |
| `gh` helpers in dashboard | Keep. New helpers added: `gh_get_pr_state` for `pr-merge-confirmation`. |

## 3. The new code we write

These are net-new modules:

| Module | What it does |
|---|---|
| `shared/models/workflow.py` | The new YAML schema: `Workflow`, `Variables`, `ArtifactNode | CodeNode | LoopNode`, `VariableDecl` discriminated union per type. |
| `shared/types/` | One module per variable type: `pr.py`, `branch.py`, `review_verdict.py`, `bug_report.py`, `design_spec.py`, `impl_spec.py`, `impl_plan.py`, `pr_merge_confirmation.py`, `summary.py`, `job_request.py`. Each implements the `VariableType` protocol (§1.4 of design). |
| `shared/types/registry.py` | Closed-set type registry. Engine's `list[T]` and `Maybe[T]` parametric wrappers. |
| `shared/types/protocol.py` | `VariableType` protocol + `NodeContext` + `PromptContext` + `FormSchema`. |
| `shared/envelope.py` | Engine-owned variable envelope: `{type, version, repo, producer_node, produced_at, value}`. Serialise/deserialise. |
| `engine/validator.py` | Static workflow validator (§4 of design). Runs at `workflow validate`, `job submit`, driver spawn. |
| `engine/resolver.py` | Variable resolver implementing §1.5 strongly-typed indexing. |
| `engine/substrate.py` | Substrate allocator (§2.4): pulls job branch, forks stage branches, recovers missing branches, manages per-iteration vs shared modes. |
| `engine/dispatcher.py` | Node dispatcher: routes `artifact` / `code` / `loop` to the right handler; calls actor; runs each output's type `produce` post-actor. |
| `engine/loop.py` | Loop iteration driver: count vs until, max_iterations, indexed variable production, outputs projection. |
| `engine/hil.py` | HIL submission API: synchronous verification, disk-first reads, public submission endpoint. |
| `engine/driver.py` | The new long-lived driver process. Replaces `job_driver/runner.py`. State-scan crash recovery on (re)start. |
| `engine/prompt.py` | Prompt assembly from per-type `render_for_producer` / `render_for_consumer`. |

## 4. The e2e test (written FIRST)

We write this before any of the implementation above.

### 4.1 Test contract

The test loads a workflow YAML, runs it end-to-end against real Claude + real GitHub, asserts outcomes:

- Job reaches `COMPLETED`.
- Every declared variable in the workflow that should have been produced was produced (in envelope form on disk).
- Every node either SUCCEEDED or was correctly SKIPPED via `runs_if`.
- Every PR variable corresponds to a real PR in the remote.
- Every branch variable corresponds to a real branch in the remote.
- The job branch in the remote contains the expected merged work (verified via gh API).
- Loop iterations completed within `max_iterations` for every loop.
- HIL pending markers are gone (all gates resolved).
- `events.jsonl` is well-formed and contains expected event types per node.

### 4.2 Test infrastructure (adapt from existing)

The current `tests/e2e/test_real_claude_lifecycle.py` has the bones:

- preflight (env vars, gh auth, claude binary).
- bootstrap (reuse `hammock-e2e-test` repo; create if absent).
- snapshot pre-existing branches.
- spawn driver.
- HIL stitcher (auto-answers human gates with predetermined typed values).
- teardown (close PRs, delete added branches, sum cost).

What changes:
- Outcome assertions are variable-shaped, not stage-shaped.
- HIL stitcher submits via the public engine API, not by reaching into cache internals.
- Test takes a workflow YAML path as a parameter so we can run the same harness against progressively complex YAMLs.

### 4.3 Test stages (the YAMLs we run progressively)

Six stages. Each adds exactly one new capability so when something breaks, the cause is unambiguous. Final test (T6) is the full fix-bug workflow.

| Stage | New capability tested | YAML scope | Lines |
|---|---|---|---|
| **T1** | Basic artifact dispatch, variable persistence with envelope, validator on a trivial DAG, single agent review | `write-bug-report` (artifact + agent) → `write-design-spec` (artifact + agent) → `review-design-spec-agent` (artifact + agent producing review-verdict) | ~50 |
| **T2** | HIL gate: human-actor dispatch, form rendering, sync submission verification, disk-first state | T1 + `review-design-spec-human` (artifact + human) | ~70 |
| **T3** | `code` kind: substrate allocation, branch hierarchy (job → stage), `pr` type's `produce` (real push + open PR) | T2 + 1 `implement` node (code + agent producing `pr`). No loop yet, no human merge gate. | ~110 |
| **T4** | `until` loop, `max_iterations`, substrate `shared`, indexing inside loop, `pr-merge-confirmation` type with GitHub verification | T3 + wrap `implement` + `pr-merge-hil` in an `until` loop on `pr_review.verdict == 'merged'` | ~150 |
| **T5** | `count` loop, nested-loop substrate (`per-iteration` outer / `shared` inner), `[*]` fan-in aggregation, cross-loop indexing | T4 + outer `count` loop with hardcoded `count: 2` (skip impl-plan-spec for now) | ~190 |
| **T6** | Full fix-bug workflow per design §6: spec/impl-spec/impl-plan blocks (until-of-until), implement-loop (count-of-until), tests-and-fix, conditional tests-pr-merge-hil, summary | Full design-patch §6 YAML | ~500 |

### 4.4 Iteration discipline

For each test stage:

1. Run the harness with that stage's YAML.
2. If it passes, advance to the next stage.
3. If it fails, the failure mode is what it is. Fix the implementation, re-run **the same stage's YAML** until green. Then advance.

When advancing breaks an earlier stage, fix and re-run all stages T1..current. Never let an earlier stage regress.

This catches bugs at the simplest possible YAML where they're easiest to localise.

## 5. Order of work

Within each step, the discipline is: **unit tests first, then implementation, then re-run all unit tests to catch regressions, then the e2e for the relevant stage.**

1. **Write the e2e test infrastructure and T1's YAML + test invocation.** Includes unit tests for the harness itself (preflight, bootstrap, cleanup, outcomes). No engine code yet — the failing e2e is the spec.
2. **Implement just enough to pass T1.** Per module: unit tests first, then the module. Modules: workflow loader, validator (basic checks), variable type registry, `artifact` kind dispatcher, persistence with envelope, `bug-report` / `design-spec` / `review-verdict` / `job-request` types, basic prompt builder, driver loop without HIL/code/loops.
3. **Run T1. Fix until green.**
4. **Advance to T2.** Add HIL submission API, disk-first state, form rendering, sync verification. Re-run T1+T2, fix any regression.
5. **Advance to T3.** Add `code` kind, substrate allocator, branch hierarchy (job + stage), `pr` type. Re-run T1+T2+T3, fix.
6. **Advance to T4.** Add `until` loop primitive, `max_iterations`, indexing inside loop, `pr-merge-confirmation` type with GitHub verification. Re-run all, fix.
7. **Advance to T5.** Add `count` loop, nested-loop substrate model, `[*]` aggregation. Re-run all, fix.
8. **Advance to T6.** Add remaining variable types (`impl-spec`, `impl-plan`, `summary`, `branch`). Stress-test full nested indexing. Re-run all, fix.
9. **Codex adversarial review on the implementation.** Submit a PR; address feedback.
10. **Merge.**

## 6. Definition of done

- All 6 test stages pass on a single contiguous run (no re-run needed between stages).
- The full fix-bug YAML produces:
  - A real PR per implement iteration, all merged into the job branch.
  - An optional test-fix PR if tests-and-fix did work, also merged.
  - A `summary.md` artefact with PR URLs.
  - All HIL gates resolved.
  - All hammock branches in the remote (job + stage branches), or correctly cleaned up by teardown.
- The validator rejects every malformed YAML it should (separate test suite of negative cases).
- No code path in the engine catches an exception silently and falls through to a degraded behaviour. Every failure surfaces as a typed error or a hard exit.

## 7. What this is NOT

- Not a port of v0's tests to the new model. The test infrastructure is adapted; the assertions are rewritten.
- Not a clean greenfield project. We reuse atomic file writes, gh helpers, worktree primitives, dashboard shell, MCP framework, CLI scaffolding. The new code is the contract layer.
- Not all-or-nothing. Each test stage gives us a working subset. If we run out of time partway through, we stop at a stage and ship that subset; later stages stay as backlog.

---

(This document is the work plan. Updates to it are commits, not redrafts.)
