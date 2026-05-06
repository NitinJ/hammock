# Real-Claude E2E Integration Test — Design

**Status:** preconditions complete (P1–P5 merged via PRs #26 #27 #28); design open for refinement before the e2e test PR
**Last updated:** 2026-05-04
**Related:**
- `docs/runbook.md § 9` — manual dogfood walk this test partially automates
- `docs/implementation.md § 9` — v1+ list (this test addresses the real-Claude execution gap)
- `docs/v0-alignment-report.md` — drift between spec and code (closed; on main)
- `docs/specs/2026-05-04-real-claude-e2e-precondition-plan.md` — implementation plan that delivered P1–P5
- `tests/e2e/test_full_lifecycle.py` — existing fake-fixture e2e (the pattern this test extends)

This document describes **what** the test does, **when** it runs, **what counts as pass**, and **what must exist** for it to run. Implementation details (file names, code shapes, fixture wiring) are deliberately deferred to the implementation plan.

---

## Goals

A fidelity check on the real execution stack, focused narrowly on what unit tests cannot reach:

1. **Catch RealStageRunner regressions early.** Verify the real-`claude` path drives a job end-to-end to `COMPLETED`.
2. **Verify storage-layer + git artifact contracts** in production-shaped form: branches, worktrees, PRs, per-stage artifacts, `summary.md`, `events.jsonl`.
3. **Surface integration-only bugs** that emerge when MCP server + Stop hook + driver IPC + dashboard + a real `claude` subprocess all run together. Unit tests can't see these.
4. **Bound scope to what we can reliably assert.** Pass = job completes + artifacts match contracts. No claims about model output content.

The first iteration runs against a single chosen job type, but **the design must be job-type-agnostic in the scaffolding**: registration, submission, polling, assertion derivation, and cleanup apply to any job type. Schema-specific HIL payload generation is the only place where the test legitimately needs per-template knowledge, and that is captured as an explicit registry rather than scattered conditionals.

## Non-goals

- Model-quality regression detection (whether Claude's output content is correct).
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
| Closed-loop HIL → artifact bridge (form-pipeline auto-resolution) | **Out** — gates are stitched the same way the existing fake e2e does |
| Default-CI execution | **Out** — opt-in only |
| Model-quality assertions | **Out** — deferred to a future test |

---

## Precondition track

The Codex design review surfaced that real-mode execution has several pre-existing gaps. The e2e test cannot succeed at all until they're closed. This track is sequenced as a series of small PRs leading up to the e2e test PR.

### Precondition PRs — **all merged**

| PR | Status | What it did |
|---|---|---|
| **P1 — Runner plumbing** | merged in #26 | `_build_runner` now passes all 6 RealStageRunner kwargs; bundled Stop-hook script ships in the wheel (#27). |
| **P2 — Real-mode prompt construction** | merged in #26 | `job_driver/prompt_builder.build_stage_prompt`: job context, declared inputs (16 KB per-file cap, 64 KB aggregate via #27), required outputs annotated with validator schemas, working directory. |
| **P3 — Dynamic stage handling** | merged in #26 | `_execute_stages` re-reads `stage-list.yaml` after `is_expander=True && SUCCEEDED`; 1000-stage cap enforced at read-time (#27). |
| **P4 — Event-taxonomy additions** | merged in #28 | `worktree_created` / `worktree_destroyed` / `worker_exit` in `EVENT_TYPES`; emission for worktree create + every stage runner return path including runner exceptions (#28 codex follow-up). `worktree_destroyed` is a taxonomy-only entry until post-v0 cleanup ships. |
| **P5 — HilItem creation on human-stage block** | merged in #28 | `shared.hil_factory.create_stage_block_hil_item`; `_block_on_human` creates the item + emits `hil_item_opened`. |

### Out of scope of the precondition track (intentionally)

- Closed-loop HIL → artifact bridge (form-pipeline auto-resolution). Still v1+ — the test stitches gates exactly as the existing fake e2e does.
- Hammock-side per-stage budget enforcement. Still v1+; the wall-clock pytest timeout is the only test-time backstop.
- Anything that changes `RealStageRunner`'s public contract beyond emitting the new events. Constructor parameters and call shape stay the same.

---

## Operator preconditions

These must be satisfied at runtime. If any is missing, the test must skip with a clear message naming the missing piece — never fail mid-run.

### Tooling

1. **`git`** — installed and on `$PATH`.
2. **`gh` CLI** — installed and authenticated such that `gh auth status` and `gh repo view <test-repo>` both succeed. Project registration uses the `gh` CLI, not raw `GITHUB_TOKEN` use; an unauthenticated `gh` will fail registration. Operators using a token must use it through `gh auth login --with-token` or a method `gh` recognises.
3. **`claude` CLI** — installed and resolvable (on `$PATH` or via `HAMMOCK_CLAUDE_BINARY`). Must be authenticated and must support the flags RealStageRunner uses today: `-p --output-format stream-json --verbose --settings`, plus `--max-budget-usd` from templates.
4. **Network reachability** to GitHub and to Claude's API endpoints.

### Test repo

5. **A dedicated GitHub test repo.** Identity is `HAMMOCK_E2E_TEST_REPO_URL` if set, else `https://github.com/<gh-user>/hammock-e2e-test` (the user is read from `gh api user --jq .login` during preflight). The test bootstraps it (D18):
   - **Repo doesn't exist yet** → test creates it as **private** via `gh repo create --private`, pushes the bundled seed content (a tiny Python program — `add_integers.py` summing N ints + a `pytest` test + `README.md`) to `main`, then **enables branch protection on `main`** (1+ approving review required, no force-push) to mirror production Hammock workflows. The seed push is the only direct write to `main`; everything after happens on feature branches.
   - **Repo exists** → test clones and hard-resets the local checkout to `origin/main` (whatever's there). Remote `main` is allowed to drift across runs as merged PRs accumulate; the test asserts contracts, not specific main-branch content.

   The seed content lives at `tests/e2e/seed_test_repo/` in the hammock repo. Branch protection on `main` is the same parity stance Hammock production assumes: claude opens PRs, a HIL "review-and-merge" gate blocks the driver, the test stitches the HIL programmatically (so the PR is not actually merged in the test). If a production template were ever to auto-merge without a HIL gate, branch protection would surface that as a real-mode failure — which is exactly the kind of integration bug this test exists to catch.

### Configuration env vars

6. **Opt-in switch (single boolean):**
   - `HAMMOCK_E2E_REAL_CLAUDE=1` — when unset, the test *skips*. When set, the rest of the env vars below are *required*; missing ones are config errors and *fail* the test rather than skip (D12).
7. **Required when opt-in is on:**
   - `HAMMOCK_E2E_JOB_TYPE` — which job type to submit (e.g. `fix-bug`, `build-feature`).
8. **Optional:**
   - `HAMMOCK_E2E_TEST_REPO_URL` — clone URL of the test repo. **Default:** derive `https://github.com/<gh-user>/hammock-e2e-test` from `gh api user --jq .login`. The test bootstraps the repo (D18) regardless of how it was named — create-if-absent, reuse-if-present.
   - `HAMMOCK_CLAUDE_BINARY` — override `claude` resolution.
   - `HAMMOCK_E2E_KEEP_ROOT=1` — preserve the tmp `HAMMOCK_ROOT` after the run for post-mortem inspection. Off by default.
   - `HAMMOCK_E2E_TIMEOUT_MIN` — wall-clock cap in minutes. Default 30 (D13).

### Project-config concerns (out of scope for this test)

`gh` / `GITHUB_TOKEN` plumbing into the spawned `claude` subprocess so it can open PRs is a project-config concern handled outside this spec. The test assumes the project's registered config carries whatever credentials claude needs; we do not assert on PR creation in the outcomes (only on branches, see #11).

### Dev dependencies — **still outstanding** (land with the e2e test PR)

8. **`pytest-timeout`** added to the dev-deps in `pyproject.toml`. The test relies on it for the wall-clock guard. (Not currently installed; verified missing on `main` 2026-05-04.)
9. **`real_claude` pytest marker registered** in `pyproject.toml`. The repo enforces `--strict-markers`, so unregistered markers fail collection. (Not currently registered; verified missing on `main` 2026-05-04.)

These two are the only mechanical changes left before the e2e test itself can ship. They land in the e2e test PR rather than as separate prereqs (each is one line in `pyproject.toml`).

---

## Preflight checks

The test exposes a single fixture that runs all of these before the scenario starts. The skip-vs-fail policy distinguishes "this environment isn't asking to run the test" (skip) from "the operator opted in but their config is wrong" (fail) — see D12.

### Skip path (opt-in not set)

| Check | Skip reason |
|---|---|
| `HAMMOCK_E2E_REAL_CLAUDE` is not `1` | `"opt-in env var not set"` |

### Fail-loud path (opt-in is set, but something else is wrong)

Once the operator has opted in, missing config or unmet environment is a real bug worth surfacing — not silently dropping the test.

| Check | Failure reason |
|---|---|
| `HAMMOCK_E2E_JOB_TYPE` is unset | `"opt-in set but HAMMOCK_E2E_JOB_TYPE missing"` |
| `git` not on `$PATH` | `"git not installed"` |
| `gh auth status` fails | `"gh CLI not authenticated"` |
| `gh repo view <test-repo>` fails for a reason **other than "not found"** (auth denied, network error, etc.) | `"test repo not viewable by gh"` |
| `claude` not resolvable | `"claude CLI not found"` |
| `claude` version doesn't support the required flags | `"claude CLI flag support insufficient"` |
| Network reachability probe fails | `"network unreachable"` |
| MCP server module is not importable under the same Python interpreter the driver will use | `"MCP server module not importable"` |

---

## When the test runs

| Trigger | Behaviour |
|---|---|
| Default `pytest` invocation | Test is collected; preflight skips. |
| Default CI (existing `e2e.yml` PR + nightly) | Same — collected, skipped. **This is intentional**; the test is in `tests/e2e/` deliberately so it travels with the rest of the e2e suite, with the skip path acting as the CI guard. The `e2e.yml` workflow does not need modification. |
| Explicit invocation: `HAMMOCK_E2E_REAL_CLAUDE=1 pytest -m real_claude ...` with all preconditions satisfied | Test runs. |
| Nightly cron in CI | **Not yet.** Deferred until the project is funded. |

### Runtime budget

| Bound | Value | Mechanism |
|---|---|---|
| Hard wall-clock timeout | 30 minutes (override via `HAMMOCK_E2E_TIMEOUT_MIN`) | `pytest-timeout` (added in this PR; see Operator preconditions §8) |
| Hammock-side per-stage budget | enforced via PR #23 (`max_budget_usd` from templates) | Per-stage spend cap honoured by `RealStageRunner` + post-check. |

Hard cost guards (warnings, soft caps, billing alerts) are still deferred — operators run this consciously. **However**, teardown reads `cost_summary.json` and logs total accrued cost (success or failure) so operators see what each run cost without trawling artifacts (D14).

---

## What the test does

A single end-to-end scenario, parameterised over job type. The test:

1. **Runs preflight checks** (above). Skips with a named reason on any miss.
2. **Sets up a clean state.** Tmp `HAMMOCK_ROOT`. Bootstraps the test repo (D18): if `gh repo view <repo>` returns "not found," `gh repo create --private` it, push `tests/e2e/seed_test_repo/` to `main`, then enable branch protection on `main` (1+ approving review, no force-push) to mirror production Hammock workflows. Otherwise, clone and hard-reset to `origin/main`. Records a snapshot of pre-existing remote branches so cleanup later only removes branches the run created. Reuses the existing `tests/conftest.py::hammock_root` pattern where applicable.
3. **Registers the cloned repo as a Hammock project** through the canonical CLI path that production uses (`hammock project register`, which delegates to `gh`).
4. **Submits a job** of the configured type via the `hammock job submit` CLI (D15) — the same path the dogfood runbook walks. The CLI path triggers the production `submit_job` → `compile_job` → `spawn_driver` chain. The real `JobDriver` is spawned. Stages execute via `RealStageRunner` — real `claude` subprocesses, real MCP server, real Stop hook.
5. **Walks through every stage.** When the job blocks on a human gate, the test stitches it the same way `tests/e2e/test_full_lifecycle.py` does today: writes the required output artifact + transitions `stage.json` to `SUCCEEDED`, AND additionally records the answer through `POST /api/hil/{id}/answer` for record fidelity (which works after P5). Driver re-spawns proceed normally. The artifact's payload is built by a **fixture-builder registry** keyed by artifact schema (e.g. `review-verdict-schema` → an "approved" verdict builder); a missing builder for a schema the run encounters is a test failure with a clear "no builder for schema X" message — not a silent skip.
6. **Waits for the job to reach a terminal state** (under the wall-clock cap).
7. **Validates the on-disk and git outcomes** against the assertion list below. Assertions are derived from the compiled stage list — they apply equally to any job type whose stages declare their `required_outputs` and artifact schemas.
8. **Cleans up — always, on success or failure.** Cleanup runs through `pytest` fixture teardown so it executes even when the test body raises or times out. Before destructive cleanup, teardown reads `cost_summary.json` and logs the total accrued cost so operators see the run's $ figure (D14). It then deletes branches that were not in the pre-run snapshot (local + remote), letting GitHub auto-close the corresponding PRs. It removes the tmp `HAMMOCK_ROOT` unless `HAMMOCK_E2E_KEEP_ROOT=1`. Cleanup failures are logged but do not mask the underlying test failure.

---

## Outcomes — what counts as PASS

All of these must hold. Failure of any one fails the test. Each outcome is derived from the compiled stage list (no per-job-type hardcoding).

### Job-level

| # | Outcome | Source of truth |
|---|---|---|
| 1 | Job state reaches `COMPLETED` | `job.json` |
| 2 | Every stage in the compiled stage list (including stages appended at runtime by expanders) reached `SUCCEEDED` | per-stage `stage.json` |
| 3 | No stage ended in `FAILED` or `CANCELLED` | per-stage `stage.json` |

### Artifact-level

| # | Outcome | Source of truth |
|---|---|---|
| 4 | Each stage's declared `required_outputs` exist on disk | `stage-list.yaml` × directory listing |
| 5 | The Stop hook ran for each agent stage AND the stage's `stage.json` is `SUCCEEDED` (transitive trust — the hook validates artifacts; we don't re-run validators in the test, see D16) | `events.jsonl` (`hook_fired`) × `stage.json` |
| 6 | `summary.md` exists and contains a URL (PR or branch) | file content |

### Per-stage agent artifacts (concrete RealStageRunner contracts)

| # | Outcome | Source of truth |
|---|---|---|
| 7 | For every agent stage that ran, `agent0/latest/stream.jsonl` exists and is non-empty | stage run dir |
| 8 | Same for `messages.jsonl` | stage run dir |
| 9 | Same for `result.json` | stage run dir |
| 10 | Same for `stderr.log` | stage run dir |

### Git-level

| # | Outcome | Source of truth |
|---|---|---|
| 11 | At least one job branch (`hammock/jobs/<slug>`) and at least one stage branch (`hammock/stages/<slug>/<stage_id>`) are present in the test repo (verified against `dashboard/code/branches.py` 2026-05-04) | `git branch -r` |

### Event-stream-level

| # | Outcome | Source of truth |
|---|---|---|
| 12 | The event stream is well-formed JSON-lines and the stage-transition sequence is valid against the `Stage` state machine | `events.jsonl` |
| 13 | At least one `worktree_created` event is present | `events.jsonl` (depends on P4) |
| 14 | For every stage that reached `SUCCEEDED`, a matching `worker_exit` event exists with `succeeded=True` and `exit_code=0` (failed-stage `worker_exit` events are present too — but with `succeeded=False`; see *Decisions log* D11) | `events.jsonl` |

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
| Closed-loop HIL → artifact bridge integration | Separate v1+ effort |
| Cost guards / soft caps / billing alerts | Operators run this consciously |
| Nightly CI cron | Real cost on a recurring schedule conflicts with the no-funding constraint |

---

## Risks + mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Precondition track slips; one of P1–P5 turns out harder than expected | medium | e2e PR delayed | Track is a sequence of small independent PRs; the e2e PR is the closing PR and only blocks once all five land |
| Run exceeds wall-clock | medium | test timeout | 15-min `pytest-timeout`; teardown cleans up regardless |
| GH rate limit during run | low | flake | Dedicated test repo; failures retryable |
| Branch cleanup deletes a branch that was already in the test repo | low | data loss | Pre-run snapshot + diff: cleanup only deletes branches absent before the run |
| HIL stitching races driver restart | low | flake | Same pattern proven by the existing fake e2e |
| Stop-hook validation rejects an artifact Claude wrote | medium | stage FAILED | This is *real coverage* — surface as a bug in Hammock contracts or the prompt; do not paper over |
| Test repo state drift between runs | low | non-repeatable run | Hard-reset to `origin/main` in test setup |
| Fixture builder registry doesn't cover a schema a future template uses | medium | clear test failure | Failure message names the missing schema; adding a builder is a small spec-driven change |

---

## Decisions log

Each design choice with the alternatives evaluated and the reasoning for the selected option.

### D1 — Test scenario

**Selected:** a single end-to-end job run against a dedicated GitHub test repo, with the job type chosen at runtime via env var. Scaffolding is job-type-agnostic.

| Option | Verdict | Reasoning |
|---|---|---|
| Hardcode a single job type into the test | ✗ | Couples the test to one template; adding another means duplicating scaffolding |
| **Job-type-agnostic scaffolding, type chosen via env var** | ✓ | Same test serves any job type; assertions derive from the compiled stage list, not the template |
| Reuse `tests/fixtures/dogfood-bug/` | ✗ | Conflates the manual-dogfood fixture with the automated test repo |
| Custom trimmed-down template designed for testing | ✗ | Test would no longer exercise production templates |

### D2 — Test orchestration

**Selected:** match the existing e2e pattern — exercise the dashboard application through its production code paths via the established in-process test transport, plus the real detached driver subprocess that `spawn_driver` already creates.

| Option | Verdict | Reasoning |
|---|---|---|
| **Match existing e2e pattern (in-process app + real detached driver)** | ✓ | Goal is RealStageRunner + git + storage layer; none of those care about HTTP transport. Aligns with codebase convention. |
| Spin up a real dashboard subprocess on a free port | ✗ | Adds fixture complexity without testing anything new for our goals |
| Operator-started external dashboard | ✗ | Coordination burden; can't run unattended |

### D3 — State observation

**Selected:** poll the on-disk job/stage files. Same pattern as existing e2e.

| Option | Verdict | Reasoning |
|---|---|---|
| **Poll on-disk state** | ✓ | Simple, deterministic, matches existing pattern |
| Subscribe to SSE | ✗ | SSE has its own integration test; don't conflate |
| Poll the read API | ✗ | Same observability as disk, more code, no extra surface coverage |

### D4 — HIL gate handling

**Selected:** poll for `BLOCKED_ON_HUMAN`; for each gate, build the required output artifact through a **fixture-builder registry keyed by artifact schema**, write it to disk, mark `stage.json` `SUCCEEDED`, additionally `POST /api/hil/{id}/answer` for record fidelity, then re-spawn the driver. The stitching helper is shared with the existing fake e2e; it reads required output schemas from the compiled stage list and dispatches to the registry.

| Option | Verdict | Reasoning |
|---|---|---|
| Disk stitching only (existing pattern) | minimal | Doesn't exercise the answer endpoint |
| **Disk stitching + answer endpoint POST + schema-keyed fixture-builder registry** | ✓ | Adds endpoint coverage cheaply; fixture-builder registry makes per-schema knowledge explicit and addable rather than buried in conditionals |
| Configure agent to never call HIL | ✗ | Either special template (test ≠ prod) or fragile prompt engineering |
| Wait until closed-loop HIL bridge ships, rely on it | ✗ | Bridge is its own v1+ effort; would block this test indefinitely |
| Pure dynamic generation from `required_outputs` alone | ✗ | Schema-specific payloads are not derivable from path + validator metadata; would silently fail on new HIL kinds |

### D5 — MCP server

**Selected:** the real per-stage MCP server. No fake.

| Option | Verdict | Reasoning |
|---|---|---|
| **Real MCP server** | ✓ | Already per-stage scoped — no global side effects to isolate. Substituting a fake creates mock/prod divergence. HIL determinism comes from gate-stitching, not from faking the server. |
| Fake MCP server with the same tool surface | ✗ | Defeats the integration-test purpose |

### D6 — Cleanup + isolation

**Selected:** unconditional teardown via `pytest` fixture; tmp `HAMMOCK_ROOT` per run; reset test repo to a known commit on init; **snapshot pre-run remote branches and delete only the diff in teardown**; rely on GH auto-close of PRs when their branch is deleted.

| Option | Verdict | Reasoning |
|---|---|---|
| Conditional cleanup (only on success) | ✗ | Failed runs leave branches and PRs around — exactly when cleanup matters most |
| **Unconditional teardown via `pytest` fixture (always runs)** | ✓ | Cleanup is the same on success or failure; cleanup errors are logged, never mask the underlying test failure |
| Glob-pattern branch deletion based on naming convention only | ✗ | Hammock doesn't currently own all branch creation; a pattern match could delete a branch that pre-existed the run |
| **Pre-run snapshot of remote branches + diff in teardown** | ✓ | Provably touches only what this run created |
| Full GH cleanup including explicit PR close | ✗ | Adds permission requirements + extra teardown failure modes; PRs auto-close on branch deletion |
| Persistent HAMMOCK_ROOT by default | ✗ | State leaks across runs; exposed as opt-in via `HAMMOCK_E2E_KEEP_ROOT=1` |
| Reset test repo to known commit at run start | ✓ adopted | Repeatable starting state |

### D7 — Gating

**Selected:** pytest marker `real_claude` AND env var `HAMMOCK_E2E_REAL_CLAUDE=1` (both required); CI collects but skips by default. The "collected, skipped" path is **intentional** — the test lives in `tests/e2e/` so it travels with the rest of the e2e suite without needing CI workflow changes.

| Option | Verdict | Reasoning |
|---|---|---|
| Marker-only | ✗ alone | Easy to fire accidentally |
| Env-var-only | ✗ alone | Marker selection is the pytest convention |
| **Marker + env var** | ✓ | Hard to fire accidentally; clear opt-in |
| Move test outside `tests/e2e/` to dodge the existing CI glob | ✗ | Splits e2e tests across paths; CI noise from a skipped test is acceptable |
| Add to nightly CI cron | defer | Real cost on a schedule conflicts with the no-funding constraint |

### D8 — Assertion strategy

**Selected:** strategy A — contractual only.

| Option | Verdict | Reasoning |
|---|---|---|
| **A. Contractual only** | ✓ | Pass = job completes + artifacts match contracts. Reliable, model-quality independent. |
| B. Contractual + structural | defer | Useful future addition; not the first cut |
| C. Contractual + semantic | defer | Strongest signal but couples test pass to Claude's model quality — wrong oracle for an integration test |

### D9 — Precondition packaging

**Selected:** a precondition track of five small sequenced PRs (P1–P5), with the e2e test as the closing PR. Track is enumerated in the *Precondition track* section above.

| Option | Verdict | Reasoning |
|---|---|---|
| **Precondition track of five small PRs, e2e test as the closing PR** | ✓ | Each precondition is small, focused, independently reviewable; the e2e PR's review surface stays scoped to test code |
| Single megaPR containing all preconditions + the test | ✗ | Massive review surface; would entangle six independent concerns |
| Ship the e2e test against today's code and accept it failing until preconditions land | ✗ | A failing test in a test directory is noise; the value of the design exercise was to surface preconditions before writing the test |
| Defer the e2e test indefinitely until "real mode just works" by accident | ✗ | Without a forcing function the gaps don't close in any predictable order |

### D18 — Test repo bootstrap

**Selected:** the test creates the repo on first run (via `gh repo create`) and seeds it with `tests/e2e/seed_test_repo/` (a tiny `add_integers.py` + pytest + `README.md`). On subsequent runs, the existing repo is used as-is and locally hard-reset to `origin/main`.

| Option | Verdict | Reasoning |
|---|---|---|
| **Create-if-absent (private + branch-protected) + seed; reuse-if-present** | ✓ | Operator opts in once, the test handles bootstrap; subsequent runs are zero-config. Branch protection on `main` mirrors production Hammock workflows so a template that tries to auto-merge without a HIL gate fails loudly during a real-mode run — the kind of integration bug this test exists to catch. Private visibility keeps claude's experiments off public discovery. |
| Operator-provisioned, fail if missing | ✗ | Friction every first-time setup; ergonomics matter for an opt-in test that's already gated on real-cost claude. |
| Generate seed content on the fly each run | ✗ | Repo state would churn across runs — undermines repeatability for cost analysis or comparing runs. |
| Embed seed content as a string in the test | ✗ | A real directory is easier to evolve, version-control, and inspect; the test just shells out a copy + git push. |

Seed-content design intent: a *credible, small* program — small enough that a single claude stage can grok it; credible enough that fix-bug / build-feature stages have something real to land on. Drift on remote `main` (from merged claude PRs over time) is fine; the local hard-reset to `origin/main` keeps each run starting from a stable point.

### D17 — Fixture-builder registry shape

**Selected:** a plain Python dict in `tests/e2e/hil_builders.py` keyed by artifact schema name. Adding a new schema is one dict entry.

| Option | Verdict | Reasoning |
|---|---|---|
| **Plain dict in test code** | ✓ | Trivially discoverable, type-checkable, no plugin runtime; matches the test's "dumb scaffolding" stance. |
| Plugin / entry-points discovery | ✗ | Overkill; the registry has 3–6 entries and lives next to the test. |
| YAML-driven registry | ✗ | Adds parse/validation surface for no gain. |

### D16 — Trust the Stop hook for artifact validation

**Selected:** outcome #5 asserts the Stop hook fired (via `hook_fired` event) and the stage didn't transition to FAILED — instead of re-running the validator registry in-process.

| Option | Verdict | Reasoning |
|---|---|---|
| **Trust transitivity (hook fired ∧ stage SUCCEEDED ⟹ artifact valid)** | ✓ | One source of truth (the hook). Avoids re-running validators which would mask hook bugs. The hook is the production validator; if it has a bug, the e2e test should reveal it via SUCCEEDED-but-bad-artifact downstream symptoms, not by competing assertions. |
| Re-run validator registry in test | ✗ | Duplicates the production check; a hook bug would be masked by the test's re-validation. |
| Drop assertion entirely | ✗ | Loses contract coverage; the Stop hook's job is exactly this. |

### D15 — Job submission path

**Selected:** `hammock job submit` CLI.

| Option | Verdict | Reasoning |
|---|---|---|
| **`hammock job submit` CLI** | ✓ | Mirrors the runbook's dogfood flow; the CLI is what operators actually use. Same `submit_job` → `compile_job` → `spawn_driver` chain as HTTP. |
| `POST /api/jobs` via TestClient | ✗ alone | Would diverge from the operator's path; CLI is more representative. |
| Both, parameterised | ✗ | More surface, no extra coverage; pick one for repeatability. |

### D14 — Cost visibility on teardown

**Selected:** read `cost_summary.json` at teardown (success or failure) and log the total. No enforcement, just visibility.

| Option | Verdict | Reasoning |
|---|---|---|
| **Log total cost on teardown** | ✓ | Cheap signal so operators see each run's $ figure without spelunking. Doesn't enforce. |
| Enforce a soft cap | defer | Cost guards are a v1+ surface; this design defers them. |
| Silent (no log) | ✗ | Operators can't budget without visibility; the data is already on disk. |

### D13 — Wall-clock budget

**Selected:** 30-minute default, env-overridable via `HAMMOCK_E2E_TIMEOUT_MIN`.

| Option | Verdict | Reasoning |
|---|---|---|
| **30 min default + env override** | ✓ | `build-feature` runs reach 6+ stages with multi-minute claude calls; 15 min is too tight. Operators with smaller test repos can lower; CI-friendly later. |
| 15 min hardcoded | ✗ | Original guess; in practice tighter than realistic real-mode runs. |
| No timeout, rely on per-stage caps | ✗ | A wedged driver with no claude subprocess wouldn't hit per-stage caps; need a process-level kill switch. |

### D12 — Skip vs fail policy on missing config

**Selected:** `HAMMOCK_E2E_REAL_CLAUDE` unset → skip (the test doesn't apply to this environment). Anything *else* missing while opt-in is on → fail (the operator opted in but mis-configured; silent skip would mask the bug).

| Option | Verdict | Reasoning |
|---|---|---|
| **Skip on opt-in unset; fail on other misses while opt-in is on** | ✓ | Matches operator intent: "I asked you to run; tell me what's wrong." |
| Skip on every missing piece | ✗ | A missing repo URL after `HAMMOCK_E2E_REAL_CLAUDE=1` is a config bug, not "this environment can't run." Skipping hides it. |
| Fail on everything | ✗ | Loses the CI-collects-skipped property. |

### D11 — `worker_exit` outcome (post-P4 codex review)

P4's codex review wired `worker_exit` for runner exceptions too (with `succeeded=False`, `exit_code=None`). Outcome #14 had to tighten from "every subprocess emitted exit code 0" to "every SUCCEEDED stage's `worker_exit` shows `exit_code=0`."

| Option | Verdict | Reasoning |
|---|---|---|
| **Filter to SUCCEEDED stages, assert `exit_code=0`** | ✓ | Matches the runtime contract: failed/exception stages emit too, with their own payload. |
| Assert exit_code=0 across all `worker_exit` events | ✗ | Would require RED-coercing every failure path to set `exit_code=0`, which is wrong — exceptions have no exit code. |
| Drop the assertion entirely | ✗ | The whole point of P4 was to make this contract assertable. |

### D10 — HIL fixture-builder registry

**Selected:** a small explicit registry of payload builders keyed by artifact schema name (e.g. `review-verdict-schema` → "approved" verdict builder). The gate-stitcher dispatches to the registry; missing schema → loud failure with a named schema.

| Option | Verdict | Reasoning |
|---|---|---|
| **Explicit registry keyed by schema** | ✓ | Job-type-agnostic scaffolding stays generic; the schema-specific knowledge is captured in one place; adding a new HIL kind is a one-entry addition |
| Hardcoded conditionals in the gate-stitcher | ✗ | Per-job-type knowledge scattered through the test; un-extensible |
| Pure dynamic generation from `required_outputs` metadata alone | ✗ | Schemas need actual payloads; metadata is insufficient for arbitrary future HIL kinds |
| Read recorded fixtures from disk | ✗ | Adds a fixture maintenance surface and tightly couples the test to specific template versions |

---

## Acceptance criteria for this design

The design is accepted when the user confirms:

- [ ] Goals + non-goals match intent
- [ ] All design decisions reflect the conversation (alternatives + selected + reasoning preserved)
- [ ] Precondition track is correctly scoped (P1–P5)
- [ ] Operator preconditions are complete
- [ ] Preflight checks are the right set
- [ ] Outcomes (pass criteria) are the right contract to enforce
- [ ] Gating rules are correct (marker + env var; CI collected-skipped is intentional)
- [ ] Cleanup behaviour is correctly captured (unconditional, pre-run-snapshot diff, runs on success and failure)
- [ ] Job-type-agnostic intent is correctly captured, including the fixture-builder registry approach
- [ ] HIL fixture-builder registry approach (D10) is sound

Once accepted, the next step is to invoke the `superpowers:writing-plans` skill to produce implementation plans, broken into:

1. **Precondition PRs P1–P5** — one plan per PR, each focused and small.
2. **E2E test PR** — the test, the shared HIL-stitching helper extracted from the existing e2e, the fixture-builder registry, the preflight fixture, the gating, and the operator-facing runner script.
