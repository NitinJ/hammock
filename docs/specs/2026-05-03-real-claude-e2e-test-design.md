# Real-Claude E2E Integration Test — Design

**Status:** proposed — pending user written-spec review
**Date:** 2026-05-03
**Related:**
- `docs/runbook.md § 9` — manual dogfood walk this test partially automates
- `docs/implementation.md § 9` — v1+ list (this test addresses the real-Claude execution gap)
- `docs/v0-alignment-report.md` — RealStageRunner MCP/Stop-hook plumbing gap (handled by the precondition PR)
- `tests/e2e/test_full_lifecycle.py` — existing fake-fixture e2e (the pattern this test extends)

This document describes **what** the test does, **when** it runs, **what counts as pass**, and **what must exist** for it to run. Implementation details (file names, code shapes, fixture wiring) are deliberately deferred to the implementation plan.

---

## Goals

A fidelity check on the real execution stack, focused narrowly on what unit tests cannot reach:

1. **Catch RealStageRunner regressions early.** Verify the real-`claude` path drives a job end-to-end to `COMPLETED`.
2. **Verify storage-layer + git artifact contracts** in production-shaped form: branches, worktrees, PRs, per-stage artifacts, `summary.md`, `events.jsonl`.
3. **Surface integration-only bugs** that emerge when MCP server + Stop hook + driver IPC + dashboard + a real `claude` subprocess all run together. Unit tests can't see these.
4. **Bound scope to what we can reliably assert.** Pass = job completes + artifacts match contracts. No claims about model output content.

The first iteration runs against a single chosen job type, but **the design must be job-type-agnostic**: the same scaffolding (registration, submission, gate-stitching, polling, assertions) applies to any job type the test repo is configured for. Adding coverage for a new job type should mean configuring the test repo and pointing the test at it — not rewriting the test.

## Non-goals

- Model-quality regression detection (whether Claude's output is correct content).
- Frontend / Playwright coverage.
- Default-CI execution.
- Exhaustive failure-mode coverage.

---

## Scope

| Dimension | Decision |
|---|---|
| Real `claude` subprocess via `RealStageRunner` | **In** |
| Real dashboard application (running through its production code paths) | **In** |
| Real registered git project, real worktrees / branches / PRs | **In** |
| Real GitHub remote (dedicated test repo, operator-provisioned) | **In** |
| Real per-stage MCP server | **In** — no fake |
| Job-type-agnostic test scaffolding | **In** |
| Frontend / Playwright | **Out** — separate spec |
| Closed-loop HIL → artifact bridge | **Out** — gates are stitched the same way the existing fake e2e does |
| Default-CI execution | **Out** — opt-in only |
| Model-quality assertions | **Out** — deferred to a future test |

---

## Prerequisites

The test cannot run until all of these are in place. If any precondition is missing, the test must skip with a message naming the missing piece — never fail.

### Code preconditions

1. **Precondition PR (P1) merged.** RealStageRunner must receive its per-stage MCP server and Stop-hook script from the `job_driver` entry point in real mode. Today the entry point intentionally omits both (documented gap in `job_driver/__main__.py` and `docs/v0-alignment-report.md`). P1 closes that gap.
   - **Scope of P1:** wire `MCPManager` + Stop-hook script construction into the real-mode runner construction; add focused unit-test coverage mirroring the existing runner-selection tests.
   - **Out of scope of P1:** any new fakes, abstractions, or behavioural changes to `RealStageRunner`. Pure wiring.
2. **`real_claude` pytest marker registered** in `pyproject.toml`. The repo enforces `--strict-markers`, so unregistered markers fail collection.

### Operator preconditions

3. **A dedicated GitHub test repo** exists and is accessible to the test environment. Its contents and structure are an operator concern; this design does not prescribe them.
4. **`GITHUB_TOKEN`** with `repo` scope for the test repo, available in the test environment.
5. **`claude` CLI** installed and resolvable (on `$PATH` or via `HAMMOCK_CLAUDE_BINARY`).
6. **Configuration env vars set:**
   - `HAMMOCK_E2E_REAL_CLAUDE=1` — opt-in switch.
   - `HAMMOCK_E2E_TEST_REPO_URL` — clone URL of the test repo. No default; the test skips if unset.
   - `HAMMOCK_E2E_JOB_TYPE` — which job type to submit (e.g. `fix-bug`, `build-feature`). Default chosen at implementation time.
   - `HAMMOCK_E2E_KEEP_ROOT=1` — *optional*; preserves the tmp `HAMMOCK_ROOT` after the run for post-mortem inspection. Off by default.

---

## When the test runs

| Trigger | Behaviour |
|---|---|
| Default `pytest` invocation | Test is collected; skipped due to missing env var. CI stays green without workflow changes. |
| Default CI (existing `e2e.yml` PR + nightly) | Same — collected, skipped. The existing `e2e.yml` does not need modification. |
| Explicit invocation: `HAMMOCK_E2E_REAL_CLAUDE=1 pytest -m real_claude ...` with all preconditions satisfied | Test runs. |
| Nightly cron in CI | **Not yet.** Deferred until the project is funded. |

### Runtime budget

| Bound | Value | Mechanism |
|---|---|---|
| Hard wall-clock timeout | 15 minutes | pytest timeout |
| Hammock-side per-stage budget | n/a today | Hammock budget enforcement is alignment-drift item #1; not yet shipped. The wall-clock timeout is the only backstop until it ships. |

Cost guards (warnings, soft caps, billing alerts) are explicitly deferred — operators run this consciously, and the wall-clock timeout is the only protection in scope right now.

---

## What the test does

A single end-to-end scenario, parameterised over job type. The test:

1. **Sets up a clean state.** Tmp `HAMMOCK_ROOT`. Clone of the test repo at a known commit (`origin/main`, hard-reset). Reuses the existing `tests/conftest.py::hammock_root` pattern where applicable.
2. **Registers the cloned repo as a Hammock project** through the canonical CLI path that production uses.
3. **Submits a job** of the configured type through the production submission path. The real `JobDriver` is spawned. Stages execute via `RealStageRunner` — real `claude` subprocesses, real MCP server, real Stop hook.
4. **Walks through every stage.** When the job blocks on a human gate, the test stitches it the same way `tests/e2e/test_full_lifecycle.py` does today: writes the required output artifact, transitions `stage.json` to `SUCCEEDED`, and additionally records the answer through the HIL answer endpoint for fidelity. Driver re-spawns proceed normally. The stitching layer reads the gate's required output schema dynamically from the compiled stage list — no per-job-type hardcoding.
5. **Waits for the job to reach a terminal state** (under the wall-clock cap).
6. **Validates the on-disk and git outcomes** against the assertion list below. Assertions are derived from the compiled stage list — they apply equally to any job type whose stages declare their `required_outputs` and artifact schemas.
7. **Cleans up — always, on success or failure.** Cleanup runs through `pytest` fixture teardown so it executes even when the test body raises or times out. It removes branches created by the run (local + remote), letting GitHub auto-close the corresponding PRs. It removes the tmp `HAMMOCK_ROOT` unless the operator opted in to keep it via `HAMMOCK_E2E_KEEP_ROOT=1`. Cleanup failures are logged but do not mask the underlying test failure.

The first iteration is a single-scenario walk for the chosen job type. Multi-job-type and multi-scenario coverage is layered on later by the same scaffolding.

---

## Outcomes — what counts as PASS

All of these must hold. Failure of any one fails the test. Each outcome is derived from the compiled stage list (no per-job-type hardcoding).

| # | Outcome | Source of truth |
|---|---|---|
| 1 | Job state reaches `COMPLETED` | `job.json` |
| 2 | Every stage in the compiled stage list reached `SUCCEEDED` | per-stage `stage.json` |
| 3 | Each stage's declared `required_outputs` exist on disk | `stage-list.yaml` × directory listing |
| 4 | Each artifact validates against its registered schema | artifact validator registry |
| 5 | `summary.md` exists and contains a URL (PR or branch) | file content |
| 6 | At least one branch matching the job's branch namespace exists in the test repo (local or remote). The expected pattern follows the design doc convention `job/<slug>/...` (verify exact pattern during implementation against current code) | `git branch -r` |
| 7 | At least one worktree-creation event is present in the run's event stream | `events.jsonl` |
| 8 | The event stream is well-formed JSON-lines and the stage-transition sequence is valid against the `Stage` state machine | `events.jsonl` |
| 9 | Every real `claude` subprocess that ran exited with code 0 | event stream |
| 10 | No stage ended in `FAILED` or `CANCELLED` | per-stage `stage.json` |

### Explicitly NOT asserted

- "Claude produced correct output content." Would require running the test repo's own tests against the post-run worktree. Deferred to a separate test (assertion-strategy C).
- PR title / body / commit message quality. Model-quality, not Hammock-quality.

---

## What is deferred

| Item | Reason for deferral |
|---|---|
| Frontend Playwright e2e | Separate concern; browser-driven flow has its own spec |
| Strategy-C content correctness assertion (run repo's tests post-run) | Would couple test pass to model quality; separate test once strategy A is stable |
| SSE assertions | Already covered by `tests/dashboard/api/test_sse.py`; layering it in here conflates concerns |
| Multi-job-type coverage in a single run / parameterised matrix | Scaffolding is job-type-agnostic so this is a configuration change later; not first-cut |
| Hammock-side per-stage budget enforcement during the test | Depends on alignment-drift item #1 shipping |
| Closed-loop HIL → artifact bridge integration | Separate v1+ effort; gates are hand-stitched here just like the existing fake e2e |
| Cost guards / soft caps / billing alerts | Deferred — operators run this consciously |
| Nightly CI cron | Real cost on a recurring schedule conflicts with the no-funding constraint |

---

## Risks + mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Run exceeds wall-clock | medium | test timeout | 15-min pytest timeout; teardown cleans up regardless |
| GH rate limit during run | low | flake | Dedicated test repo; failures retryable |
| Branch cleanup leaves orphans | low | accumulating noise | Teardown is unconditional; cleanup failures logged but non-fatal; periodic manual sweep |
| HIL stitching races driver restart | low | flake | Same pattern proven by the existing fake e2e |
| Stop-hook validation rejects an artifact Claude wrote | medium | stage FAILED | This is *real coverage* — surface as a bug in Hammock contracts or the prompt; do not paper over |
| Test repo state drift between runs | low | non-repeatable run | Hard-reset to `origin/main` in test setup |

---

## Decisions log

Each design choice with the alternatives evaluated and the reasoning for the selected option.

### D1 — Test scenario

**Selected:** a single end-to-end job run against a dedicated GitHub test repo, with the job type chosen at runtime via env var. Scaffolding is job-type-agnostic.

| Option | Verdict | Reasoning |
|---|---|---|
| Hardcode a single job type into the test | ✗ | Couples the test to one template; adding another job type means duplicating scaffolding |
| **Job-type-agnostic scaffolding, type chosen via env var** | ✓ | Same test serves any job type; assertions derive from the compiled stage list, not the template |
| Reuse `tests/fixtures/dogfood-bug/` | ✗ | Conflates the manual-dogfood fixture (doc-grade) with the automated test repo |
| Custom trimmed-down template designed for testing | ✗ | Test would no longer exercise production templates |

### D2 — Test orchestration

**Selected:** match the existing e2e pattern — exercise the dashboard application through its production code paths via the established in-process test transport, plus the real detached driver subprocess that `spawn_driver` already creates.

| Option | Verdict | Reasoning |
|---|---|---|
| **Match existing e2e pattern (in-process app + real detached driver)** | ✓ | The test goal is RealStageRunner + git + storage layer; none of those care about HTTP transport. Aligns with codebase convention. |
| Spin up a real dashboard subprocess on a free port | ✗ | Adds fixture complexity (port discovery, readiness probe, log capture) without testing anything new for our goals |
| Operator-started external dashboard | ✗ | Coordination burden; can't run unattended |

### D3 — State observation

**Selected:** poll the on-disk job/stage files. Same pattern as existing e2e.

| Option | Verdict | Reasoning |
|---|---|---|
| **Poll on-disk state** | ✓ | Simple, deterministic, matches existing pattern |
| Subscribe to SSE | ✗ | SSE has its own integration test; don't conflate |
| Poll the read API | ✗ | Same observability as disk, more code, no extra surface coverage |

### D4 — HIL gate handling

**Selected:** poll for `BLOCKED_ON_HUMAN`; for each, write the required output artifact, mark `stage.json` `SUCCEEDED`, additionally POST to the HIL answer endpoint for record fidelity, then re-spawn the driver. The stitching helper is shared with the existing fake e2e and reads the required output schema from the compiled stage list (no per-job-type hardcoding).

| Option | Verdict | Reasoning |
|---|---|---|
| Disk stitching only (existing pattern) | minimal | Doesn't exercise the answer endpoint |
| **Disk stitching + HIL answer endpoint POST** | ✓ | Adds endpoint coverage cheaply; HIL record correctly transitions to `answered` |
| Configure agent to never call HIL | ✗ | Either special template (test ≠ prod) or fragile prompt engineering |
| Wait until closed-loop HIL bridge ships, rely on it | ✗ | Bridge is its own v1+ effort; would block this test indefinitely |

### D5 — MCP server

**Selected:** the real per-stage MCP server. No fake.

| Option | Verdict | Reasoning |
|---|---|---|
| **Real MCP server** | ✓ | Already per-stage scoped — no global side effects to isolate. Substituting a fake creates mock/prod divergence, exactly the class of bug this test is meant to catch. HIL determinism comes from gate-stitching, not from faking the server. |
| Fake MCP server with the same tool surface | ✗ | Defeats the integration-test purpose |

### D6 — Cleanup + isolation

**Selected:** unconditional teardown via `pytest` fixture; tmp `HAMMOCK_ROOT` per run; reset test repo to a known commit on init; delete created branches in teardown; rely on GH auto-close of PRs when their branch is deleted.

| Option | Verdict | Reasoning |
|---|---|---|
| Conditional cleanup (only on success) | ✗ | Failed runs leave branches and PRs around — exactly when cleanup matters most |
| **Unconditional teardown via `pytest` fixture (always runs)** | ✓ | Cleanup is the same on success or failure; cleanup errors are logged, never mask the underlying test failure |
| Full GH cleanup including explicit PR close | ✗ | Adds permission requirements and extra teardown failure modes; PRs auto-close on branch deletion |
| Persistent HAMMOCK_ROOT by default | ✗ | State leaks across runs; exposed as opt-in via `HAMMOCK_E2E_KEEP_ROOT=1` |
| Reset test repo to known commit at run start | ✓ adopted | Repeatable starting state |

### D7 — Gating

**Selected:** pytest marker `real_claude` AND env var `HAMMOCK_E2E_REAL_CLAUDE=1` (both required); CI collects but skips by default.

| Option | Verdict | Reasoning |
|---|---|---|
| Marker-only | ✗ alone | Easy to fire accidentally |
| Env-var-only | ✗ alone | Marker selection is the pytest convention; not using one leaves the test invisible to `--collect-only -m` |
| **Marker + env var** | ✓ | Hard to fire accidentally; clear opt-in |
| Add to nightly CI cron | defer | Real cost on a schedule conflicts with the no-funding constraint |

### D8 — Assertion strategy

**Selected:** strategy A — contractual only.

| Option | Verdict | Reasoning |
|---|---|---|
| **A. Contractual only** | ✓ | Pass = job completes + artifacts match contracts. Reliable, model-quality independent. |
| B. Contractual + structural (right files touched, summary mentions specific keywords) | defer | Useful future addition; not the first cut |
| C. Contractual + semantic (run repo's tests against post-run code) | defer | Strongest signal but couples test pass to Claude's model quality — wrong oracle for an integration test |

### D9 — Precondition PR (P1) packaging

**Selected:** ship the runner-selection plumbing fix as its own small PR before this test PR.

| Option | Verdict | Reasoning |
|---|---|---|
| **Separate P1 PR, then e2e test PR** | ✓ | P1 is mechanical and reviewable in isolation; bundling inflates the e2e PR's review surface and entangles concerns |
| Single PR containing both | ✗ | Larger blast radius; harder to review |

---

## Acceptance criteria for this design

The design is accepted when the user confirms:

- [ ] Goals + non-goals match intent
- [ ] All design decisions reflect the conversation (alternatives + selected + reasoning preserved)
- [ ] Prerequisites list is complete
- [ ] Outcomes (pass criteria) are the right contract to enforce
- [ ] Gating rules are correct
- [ ] P1 scope is correct
- [ ] Cleanup behaviour is correctly captured (unconditional, runs on success and failure)
- [ ] Job-type-agnostic intent is correctly captured

Once accepted, the next step is to invoke the `superpowers:writing-plans` skill to produce an implementation plan, broken into:

1. **Precondition PR — P1.** Plumb MCP server + Stop hook from the `job_driver` entry point into RealStageRunner; register the `real_claude` marker.
2. **E2E test PR.** The test, the shared HIL-stitching helper extracted from the existing e2e, the gating, and the operator-facing runner script.
