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

1. **Catch RealStageRunner regressions early.** Verify the real-`claude` path drives a `fix-bug` job to `COMPLETED` end-to-end.
2. **Verify storage-layer + git artifact contracts** in production-shaped form: branches, worktrees, PRs, per-stage artifacts, `summary.md`, `events.jsonl`.
3. **Surface integration-only bugs** that emerge when MCP server + Stop hook + driver IPC + dashboard + a real `claude` subprocess all run together. Unit tests can't see these.
4. **Bound scope to what we can reliably assert.** Pass = job completes + artifacts match contracts. No claims about model output quality.

## Non-goals

- Model-quality regression detection ("did Claude actually fix the bug").
- Frontend / Playwright coverage.
- Default-CI execution.
- Exhaustive failure-mode coverage.

---

## Scope

| Dimension | Decision |
|---|---|
| Real `claude` subprocess via `RealStageRunner` | **In** — cost accepted |
| Real dashboard application (running through its production code paths) | **In** |
| Real registered git project, real worktrees / branches / PRs | **In** |
| Real GitHub remote (dedicated test repo) | **In** — user provisions |
| Real per-stage MCP server | **In** — no fake |
| Frontend / Playwright | **Out** — separate spec |
| Closed-loop HIL → artifact bridge | **Out** — gates are stitched the same way the existing fake e2e does |
| Default-CI execution | **Out** — opt-in only |
| Model-quality assertions | **Out** — deferred to a future test |

---

## Prerequisites

The test cannot run until all of these are in place.

### Code preconditions

1. **Precondition PR (P1) merged.** RealStageRunner must receive its per-stage MCP server and Stop-hook script from the `job_driver` entry point in real mode. Today the entry point intentionally omits both (documented gap in `job_driver/__main__.py` and `docs/v0-alignment-report.md`). P1 closes that gap.
   - **Scope of P1:** wire `MCPManager` + Stop-hook script construction into the real-mode runner construction; add focused unit-test coverage mirroring the existing runner-selection tests.
   - **Out of scope of P1:** any new fakes, abstractions, or behavioural changes to `RealStageRunner`. Pure wiring.
2. **`real_claude` pytest marker registered** in `pyproject.toml`. The repo enforces `--strict-markers`, so unregistered markers fail collection.

### Operator preconditions

3. **Dedicated GitHub test repo.** A throwaway repo with one trivial Python bug (e.g. off-by-one in a small parser), a unit-test demonstrating the bug, `pyproject.toml`, and `CLAUDE.md`. Small enough that an agent fixes it in 1–2 turns per stage.
4. **`GITHUB_TOKEN`** with `repo` scope for the test repo, available in the test environment.
5. **`claude` CLI** installed and resolvable (on `$PATH` or via `HAMMOCK_CLAUDE_BINARY`).
6. **Configuration env vars set:**
   - `HAMMOCK_E2E_REAL_CLAUDE=1` — opt-in switch.
   - `HAMMOCK_E2E_TEST_REPO_URL` — clone URL of the test repo. No default; the test skips with a clear message if unset.
   - `HAMMOCK_E2E_KEEP_ROOT=1` — *optional*; preserves the tmp `HAMMOCK_ROOT` after the run for post-mortem inspection.

If any precondition is missing, the test must skip with a message naming the missing piece — never fail.

---

## When the test runs

| Trigger | Behaviour |
|---|---|
| Default `pytest` invocation | Test is collected; skipped due to missing env var. CI stays green without workflow changes. |
| Default CI (existing `e2e.yml` PR + nightly) | Same — collected, skipped. The existing `e2e.yml` does not need modification. |
| Explicit invocation: `HAMMOCK_E2E_REAL_CLAUDE=1 pytest -m real_claude ...` with all preconditions satisfied | Test runs. |
| Nightly cron in CI | **Not yet.** Deferred until the project is funded. |

### Runtime budgets

| Bound | Value | Mechanism |
|---|---|---|
| Hard wall-clock timeout | 15 minutes | pytest timeout |
| Soft cost cap | $5 | Warning printed to stderr at end of run; not a failure (cost varies with model pricing and is a flaky pass condition) |
| Hammock-side per-stage budget | n/a today | Hammock budget enforcement is alignment-drift item #1; not yet shipped. The wall-clock timeout is the only backstop until it ships. |

---

## What the test does

A single end-to-end scenario. The test:

1. **Sets up a clean state.** Tmp `HAMMOCK_ROOT`. Clone of the test repo at a known commit (`origin/main`, hard-reset). Reuses the existing `tests/conftest.py::hammock_root` pattern where applicable.
2. **Registers the cloned repo as a Hammock project** through the canonical CLI path that production uses.
3. **Submits a `fix-bug` job** through the production submission path. The real `JobDriver` is spawned. Stages execute via `RealStageRunner` — real `claude` subprocesses, real MCP server, real Stop hook.
4. **Walks through every stage.** When the job blocks on a human gate, the test stitches it the same way `tests/e2e/test_full_lifecycle.py` does today: writes the required output artifact, transitions `stage.json` to `SUCCEEDED`, and additionally records the answer through the HIL answer endpoint for fidelity. Driver re-spawns proceed normally.
5. **Waits for the job to reach a terminal state** (under the wall-clock cap).
6. **Validates the on-disk and git outcomes** against the assertion list below.
7. **Cleans up.** Branches matching the job's branch namespace are deleted (local + remote). PRs auto-close on branch deletion. Tmp `HAMMOCK_ROOT` removed unless `HAMMOCK_E2E_KEEP_ROOT=1`.

The test is a single-scenario `fix-bug` walk. Multi-scenario coverage (`build-feature`, error-path variants, etc.) is explicitly out of scope; future tests can layer on.

---

## Outcomes — what counts as PASS

All of these must hold. Failure of any one fails the test.

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

- "The bug was actually fixed." Would require running the fixture's tests against the post-fix worktree. Deferred to a separate test (assertion-strategy C).
- PR title / body / commit message quality. Model-quality, not Hammock-quality.
- Total cost. Warning only.

---

## What is deferred

| Item | Reason for deferral |
|---|---|
| Frontend Playwright e2e | Separate concern; browser-driven flow has its own spec |
| "Did Claude actually fix the bug" assertion (strategy C) | Would couple test pass to model quality; separate test once strategy A is stable |
| SSE assertions | Already covered by `tests/dashboard/api/test_sse.py`; layering it in here conflates concerns |
| `build-feature` template coverage | Larger stage list, more cost; revisit after `fix-bug` is stable |
| Hammock-side per-stage budget enforcement during the test | Depends on alignment-drift item #1 shipping |
| Closed-loop HIL → artifact bridge integration | Separate v1+ effort; gates are hand-stitched here just like the existing fake e2e |
| Nightly CI cron | Real cost on a recurring schedule conflicts with the no-funding constraint |

---

## Risks + mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Claude wanders off / runs long | medium | high cost | 15-min wall-clock timeout; soft cost warning at $5 |
| GH rate limit during run | low | flake | Dedicated test repo; failures retryable |
| Branch cleanup leaves orphans | low | accumulating noise | Cleanup failures logged but non-fatal; periodic manual sweep |
| HIL stitching races driver restart | low | flake | Same pattern proven by the existing fake e2e |
| Stop-hook validation rejects an artifact Claude wrote | medium | stage FAILED | This is *real coverage* — surface as a bug in Hammock contracts or the prompt; do not paper over |
| Model-pricing change spikes cost | low | warning only | Cost is a warning, not a failure |
| Test repo state drift between runs | low | non-repeatable run | Hard-reset to `origin/main` in test setup |

---

## Decisions log

Each design choice with the alternatives evaluated and the reasoning for the selected option.

### D1 — Test scenario

**Selected:** `fix-bug` job on a dedicated, throwaway GitHub repo containing one trivial Python bug.

| Option | Verdict | Reasoning |
|---|---|---|
| `fix-bug` on existing `tests/fixtures/dogfood-bug/` | ✗ | Conflates the manual-dogfood fixture (doc-grade) with the automated test |
| **`fix-bug` on a dedicated test repo** | ✓ | Clean separation; user is provisioning it; independent maintenance |
| `build-feature` on the same repo | defer | Larger stage list and cost; not the right first cut |
| Custom trimmed-down template | ✗ | Test would no longer exercise the production template — defeats the point |

### D2 — Test orchestration

**Selected:** match the existing e2e pattern — exercise the dashboard application through its production code paths via the established in-process test transport, plus the real detached driver subprocess that `spawn_driver` already creates.

| Option | Verdict | Reasoning |
|---|---|---|
| **Match existing e2e pattern (in-process app + real detached driver)** | ✓ | The test goal is RealStageRunner + git + storage layer; none of those care about HTTP transport. Aligns with codebase convention. |
| Spin up a real dashboard subprocess on a free port | ✗ | Adds fixture complexity (port discovery, readiness probe, log capture) without testing anything new for our goals. Reconsider only if we want to catch lifespan-only bugs in a future test. |
| Operator-started external dashboard | ✗ | Coordination burden; can't run unattended |

### D3 — State observation

**Selected:** poll the on-disk job/stage files. Same pattern as existing e2e.

| Option | Verdict | Reasoning |
|---|---|---|
| **Poll on-disk state** | ✓ | Simple, deterministic, matches existing pattern |
| Subscribe to SSE | ✗ | SSE has its own integration test; don't conflate |
| Poll the read API | ✗ | Same observability as disk, more code, no extra surface coverage |

### D4 — HIL gate handling

**Selected:** poll for `BLOCKED_ON_HUMAN`; for each, write the required output artifact, mark `stage.json` `SUCCEEDED`, additionally POST to the HIL answer endpoint for record fidelity, then re-spawn the driver. Helper lifted from existing e2e and shared.

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

**Selected:** tmp `HAMMOCK_ROOT` per run; reset test repo to a known commit on init; delete created branches in teardown; rely on GH auto-close of PRs when their branch is deleted.

| Option | Verdict | Reasoning |
|---|---|---|
| Full GH cleanup including explicit PR close | ✗ | Adds permission requirements + extra teardown failure modes |
| **Tmp HAMMOCK_ROOT + branch delete; PRs auto-close** | ✓ | Simple, low blast radius |
| Persistent HAMMOCK_ROOT by default | ✗ | State leaks across runs; exposed as opt-in via `HAMMOCK_E2E_KEEP_ROOT=1` |
| Reset test repo to known commit at run start | ✓ adopted | Repeatable starting state; only force-pushes a non-`main` working ref, not `main` |

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
| B. Contractual + structural (right files touched, summary mentions bug) | defer | Useful future addition; not the first cut |
| C. Contractual + semantic (run fixture's tests against post-fix code) | defer | Strongest signal but couples test pass to Claude's model quality — wrong oracle for an integration test |

### D9 — Soft cost-cap mechanism

**Selected:** print to stderr at end of run.

| Option | Verdict | Reasoning |
|---|---|---|
| **`print(..., file=sys.stderr)`** | ✓ | Visible in pytest output; not affected by the repo's `filterwarnings = ["error"]` policy |
| `warnings.warn(...)` | ✗ | The repo promotes warnings to errors; would convert a soft signal into a test failure |
| Logger | acceptable | Equivalent to print for this purpose |

### D10 — Precondition PR (P1) packaging

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

Once accepted, the next step is to invoke the `superpowers:writing-plans` skill to produce an implementation plan, broken into:

1. **Precondition PR — P1.** Plumb MCP server + Stop hook from the `job_driver` entry point into RealStageRunner; register the `real_claude` marker.
2. **E2E test PR.** The test, the shared HIL-stitching helper extracted from the existing e2e, the gating, the operator-facing runner script.
