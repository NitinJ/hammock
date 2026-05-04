# Real-Claude E2E Integration Test — Design

**Status:** proposed — pending user written-spec review
**Date:** 2026-05-03
**Authors:** Nitin (driver), Claude (scribe)
**Related:**
- `docs/runbook.md § 9` (manual dogfood walk this test automates)
- `docs/implementation.md § 9` (v1+ list — this test closes the "frontend Playwright e2e smoke" item *partially*: it gives us automated real-Claude coverage without the frontend)
- `tests/e2e/test_full_lifecycle.py` (existing fake-fixture e2e — pattern this design extends)
- `docs/v0-alignment-report.md § "Already-deferred items"` (RealStageRunner MCP/Stop-hook plumbing gap — addressed by precondition PR P1)

---

## Goals

This test is a **fidelity check on the real execution stack**, focused narrowly on what unit tests cannot reach.

1. **Catch RealStageRunner regressions early.** Does the real-claude path actually drive a `fix-bug` job to `COMPLETED` end-to-end?
2. **Verify storage-layer + git artifact contracts** in production-shaped form: branches, worktrees, PRs, per-stage artifacts, `summary.md`, `events.jsonl`.
3. **Surface integration-only bugs** that emerge when `MCPManager` + Stop hook + driver IPC + dashboard server + a real `claude` subprocess run together. Unit tests cannot see these.
4. **Bound scope to what we can reliably assert.** Pass = job completes + artifacts match contracts. No claims about model output quality.

**Non-goals.** Model-quality regression detection; frontend / Playwright coverage; default-CI gating; exhaustive failure-mode coverage. All deferred.

---

## Scope (locked from brainstorming)

| Dimension | Decision |
|---|---|
| Real `claude` subprocess via `RealStageRunner` | **In** — cost accepted |
| Real dashboard server (subprocess, real port) | **In** |
| Real registered git project, real worktrees / branches / PRs | **In** |
| Real GitHub remote (dedicated test repo) | **In** — user provisions |
| Real per-stage MCP server (`MCPManager`) | **In** — no fake |
| Frontend / Playwright | **Out** — deferred |
| Closed-loop HIL → artifact bridge | **Out** — gates hand-stitched as in existing fake e2e |
| Default-CI execution | **Out** — opt-in only |
| Model-quality assertions (did Claude actually fix the bug?) | **Out** — deferred to a future strategy-C test |

---

## Section 1 — Test scenario

**Decision: `fix-bug` job on a dedicated throwaway GitHub repo containing a small Python package with one trivial bug.**

### Alternatives considered

| Option | Verdict | Reasoning |
|---|---|---|
| `fix-bug` on existing `tests/fixtures/dogfood-bug/` (after pushing it to GH) | ✗ | Conflates the manual-dogfood fixture with the automated test. `expected-fix.md` is worthless under contractual-only assertions, and tying the dogfood walk to CI-style assertions creates churn pressure on a doc-grade fixture. |
| **`fix-bug` on a new dedicated test repo** | ✓ **chosen** | Clean separation from the dogfood walk. The user is already creating it. Independent maintenance lifecycle. |
| `build-feature` on the new repo | ✗ defer | Larger stage list, more HIL gates, more Claude turns, more cost. Wrong choice for first cut; revisit once `fix-bug` is stable. |
| Custom trimmed-down template designed for testing | ✗ | Test would no longer exercise the production `fix-bug` template — defeats the integration-test purpose; a regression in `fix-bug` would not be caught. |

### Repo shape

- One Python package, one trivial bug (e.g. off-by-one in a range parser, or a wrong comparator in a sort key).
- One unit-test file demonstrating the bug.
- `pyproject.toml`, `CLAUDE.md` describing the project for the agent.
- Small enough that a competent agent fixes it in 1–2 turns per stage. Target run: < 10 min, < $3.

### Stage coverage

The test reads `stage-list.yaml` after compilation and asserts contracts against the *actual* compiled stages — no hardcoding. So the test stays valid as the `fix-bug` template evolves, as long as the artifact contracts hold.

---

## Section 2 — Test orchestration

**Decision: dashboard spawned as a real subprocess via a pytest fixture; HTTP calls hit a free port; teardown SIGTERMs the process.**

### Alternatives considered

| Option | Verdict | Reasoning |
|---|---|---|
| `TestClient` + `lifespan` (existing e2e pattern) | ✗ | Doesn't exercise real ASGI server, port binding, or process lifecycle — fails the "real dashboard" goal. |
| **Subprocess fixture binding a free port** | ✓ **chosen** | Real HTTP, real lifecycle, mirrors production. Modest extra fixture code (port discovery, readiness probe, teardown). |
| Operator-started external dashboard | ✗ | Coordination burden; can't run unattended in a single `pytest` invocation. |

### Implementation sketch

- `dashboard_subprocess` pytest fixture:
  - Picks a free port via `socket.bind(("127.0.0.1", 0))`, then closes the socket immediately before launching.
  - Sets `HAMMOCK_ROOT` to a tmp dir.
  - Launches `python -m dashboard --port <p>` as `subprocess.Popen` with merged stdout/stderr captured to a tmp log.
  - Polls `GET /api/health` until 200 (or timeout, e.g. 10 s).
  - Yields the base URL.
  - On teardown: SIGTERM → wait 5 s → SIGKILL fallback. Test-failure logs include the dashboard log.
- Project registration: invokes `uv run hammock project register <clone-path>` as a subprocess (the canonical CLI path that production uses).
- Job submission: `httpx.post(f"{base}/api/jobs", json=...)`. Spawns the driver as a side-effect of the API call (existing dashboard behaviour).

---

## Section 3 — Polling vs SSE for state transitions

**Decision: poll `job.json` and `stage.json` on disk via a small helper. SSE not exercised in this test.**

### Alternatives considered

| Option | Verdict | Reasoning |
|---|---|---|
| Subscribe to `/sse/events?slug=<job>` and react to events | ✗ | SSE has its own integration test (`tests/dashboard/api/test_sse.py`); don't conflate concerns. More client code (event filter, idle handling) for no extra coverage. |
| **Poll JSON files on disk** | ✓ **chosen** | Simple, deterministic, no race window. Mirrors the existing fake-fixture e2e pattern. |
| Poll `GET /api/jobs/<slug>` over HTTP | ✗ | Same observability as disk poll, more code, no extra surface coverage. |

### Notes

A follow-up test can layer SSE assertions once this test's shape is stable. That belongs in a separate spec.

---

## Section 4 — HIL stitching

**Decision: poll for `BLOCKED_ON_HUMAN`; for each blocked stage, write the required output artifact + flip `stage.json` → `SUCCEEDED` + POST `/api/hil/<id>/answer` for record-fidelity + restart the driver.**

### Alternatives considered

| Option | Verdict | Reasoning |
|---|---|---|
| Hand-stitch on disk only (existing fake e2e pattern) | minimal | Doesn't exercise the HIL answer endpoint at all. |
| **Hand-stitch on disk + POST `/api/hil/<id>/answer`** | ✓ **chosen** | Adds real-surface coverage of the answer endpoint cheaply. Roughly one extra API call per gate. The HIL item record correctly transitions to `answered` instead of being orphaned in `awaiting`. |
| Configure the agent to never call HIL | ✗ | Either requires a special template (test ≠ prod path — defeats the point) or relies on prompt engineering to suppress HIL (fragile and brittle to model changes). |
| Build the closed-loop HIL → artifact bridge first, let the test rely on it | ✗ | That bridge is its own v1+ effort; would block this test indefinitely on unrelated work. |

### Implementation

Lift `_resolve_human_gate` from `tests/e2e/test_full_lifecycle.py` into a shared helper (e.g. `tests/e2e/_hil_stitching.py`); both tests use it.

---

## Section 5 — Cleanup + isolation

**Decision: tmp `HAMMOCK_ROOT` per run; reset test repo to a known commit on init; delete created branches in teardown; rely on GH auto-close of PRs when their branch is deleted.**

### Alternatives considered

| Option | Verdict | Reasoning |
|---|---|---|
| Tmp `HAMMOCK_ROOT` + full GH cleanup including `gh pr close` | ✗ | Adds permission requirements + extra failure modes during teardown (cleanup failure leaks orphaned branches/PRs). Diminishing returns. |
| **Tmp `HAMMOCK_ROOT` + branch delete; PRs auto-close** | ✓ **chosen** | Simple, low blast radius. Branch deletion is the well-defined operation. |
| Persistent `HAMMOCK_ROOT` (no tmp) | ✗ default | Useful for post-mortem inspection — exposed as `HAMMOCK_E2E_KEEP_ROOT=1` opt-in flag. |
| Reset test repo to a known commit on each run | ✓ **adopted** | Repeatable starting state. Requires only normal force-push to a non-`main` ref; reset target is `origin/main` so no force-push to `main` is involved. |

### Concretely

- **Init.** Clone test repo into a tmp clone path; `git fetch origin && git reset --hard origin/main`.
- **Cleanup.** Branches matching the job-scoped naming convention (expected: `hammock/<job-slug>/*`; verify during implementation) are listed and deleted (both local and remote) in fixture teardown.
- **`HAMMOCK_ROOT`.** Removed unless `HAMMOCK_E2E_KEEP_ROOT=1`.
- **GH auth.** `GITHUB_TOKEN` env var must be set with `repo` scope for the test repo. Test skips with a clear message if missing.

---

## Section 6 — Gating + runtime

**Decision: pytest marker `@pytest.mark.real_claude` AND env var `HAMMOCK_E2E_REAL_CLAUDE=1` (both required); not in default CI; 15-minute hard timeout; soft cost-cap warning.**

### Alternatives considered

| Option | Verdict | Reasoning |
|---|---|---|
| Marker-only gating | ✗ alone | Easy to fire accidentally via `pytest -m ''` or by misconfigured `pyproject.toml`. |
| Env-var-only gating | ✗ alone | Marker-based selection is the pytest convention; not using it leaves the test invisible to `pytest --collect-only -m`. |
| **Both marker + env var (belt + suspenders)** | ✓ **chosen** | Hard to fire accidentally. Clear opt-in semantics. |
| Add to a nightly CI cron | ✗ defer | Real $$ on a recurring schedule conflicts with the no-funding constraint. GH and Claude rate limits add operational overhead. Reconsider once Hammock is funded. |

### Runtime budgets

- **Hard timeout:** 15 minutes via `@pytest.mark.timeout(900)`.
- **Soft cost cap:** read `events.jsonl` cost rollup at end; emit a `UserWarning` via `warnings.warn()` if total > $5. Don't fail the test on cost — cost is model-quality dependent and a flaky pass condition.
- **Note on Hammock budget enforcement.** Hammock's own per-stage budget enforcement is alignment-report drift item #1 and not yet shipped. The pytest hard timeout is therefore the only backstop today. Once budget enforcement ships, the test can additionally configure stage-level `max_budget_usd`.

---

## Section 7 — Contractual assertions

**Decision: assert each item below; nothing about content quality.**

| # | Assertion | Source of truth |
|---|---|---|
| 1 | `job.json.state == "COMPLETED"` | storage |
| 2 | Every stage in the compiled `stage-list.yaml` reached `SUCCEEDED` | per-stage `stage.json` |
| 3 | Each stage's declared `required_outputs` exist on disk | `stage-list.yaml` × directory listing |
| 4 | Each artifact validates against its registered schema | `shared/artifact_validators.py` |
| 5 | `summary.md` exists and contains a URL (PR or branch) | regex on file content |
| 6 | ≥1 branch matching the convention RealStageRunner uses for job-scoped branches exists in the test repo (local or remote). Expected pattern (to be verified during implementation): `hammock/<job-slug>/*` | `git branch -r` |
| 7 | ≥1 worktree creation event present in `events.jsonl` | event log |
| 8 | `events.jsonl` is well-formed JSON-lines and the stage-transition sequence is valid per the `Stage` state machine | jsonl parse + state-machine check |
| 9 | Each real `claude` subprocess exited 0 (no `worker_exit` event with non-zero code) | event log |
| 10 | No stage ended in `FAILED` or `CANCELLED` | per-stage `stage.json` |

### Explicitly NOT asserted

- "The bug was actually fixed." Would require running the fixture's pytest suite against the post-fix worktree (assertion-strategy C). Deferred to a separate test.
- PR title / body / commit message quality. Model-quality, not Hammock-quality.
- Exact cost. Warning only; failing on cost would couple test stability to model-pricing changes.

---

## Section 8 — Precondition PR (P1)

**Decision: ship the runner-selection plumbing fix as its own small PR before the e2e test lands.**

### Why a separate PR

The plumbing fix is mechanical, reviewable in isolation, and has its own focused unit-test coverage (mirrors `tests/job_driver/test_main_runner_selection.py`). Bundling it with the e2e test would inflate the e2e PR's review surface and entangle two unrelated concerns.

### Scope

1. `job_driver/__main__.py` — when running in real mode (no `--fake-fixtures`), construct `MCPManager` + Stop-hook script path and pass them into `RealStageRunner`.
2. `Settings` — add a field for the Stop-hook script path (or compute from package data and document the precedence).
3. **Tests.** Focused unit test asserting `MCPManager` and Stop-hook are wired when the real path is selected. Mirrors the existing runner-selection tests in `tests/job_driver/test_main_runner_selection.py`.
4. **Not in scope.** Any new fakes, abstractions, or test infrastructure. P1 expects `RealStageRunner` already accepts `mcp_manager`, `stop_hook_path`, and `hammock_root` parameters (per `docs/v0-alignment-report.md`); confirm during P1 and, if a parameter is missing, add it as part of P1 with focused unit-test coverage. The intent remains "pure wiring" — no behaviour changes, no new abstractions.

### Acceptance

P1 lands; the next-stage e2e test PR is authored against a stable real-mode entry point. No further changes to `__main__` should be required for the e2e to function.

---

## File layout (proposed)

```
tests/e2e/
  test_full_lifecycle.py              # existing fake-fixture e2e (unchanged)
  test_real_lifecycle.py              # NEW — this design
  _hil_stitching.py                   # NEW — shared helper, lifted from existing test
  _dashboard_fixture.py               # NEW — subprocess fixture
  conftest.py                         # extended with the markers + env-var skips

scripts/
  run-e2e-real.sh                     # NEW — thin wrapper: sets env vars + invokes pytest -m real_claude -s

docs/specs/
  2026-05-03-real-claude-e2e-test-design.md   # this doc
```

The test repo itself lives outside the `hammock/` repo. Its URL is configured via the `HAMMOCK_E2E_TEST_REPO_URL` env var. There is no default — the test skips with a clear message if the env var is unset, the same way it skips if `GITHUB_TOKEN` is missing.

---

## Open questions resolved during brainstorming

| Question | Resolution |
|---|---|
| Use real or fake MCP server? | **Real.** Already per-stage scoped; no global side effects to isolate. Substituting a fake creates mock/prod divergence — exactly what this test is supposed to catch. HIL determinism is achieved by hand-stitching gates, not by faking the server. |
| Use the `dogfood-bug` fixture? | **No.** Conflates manual-dogfood with automated-test concerns. New dedicated repo. |
| Include the frontend? | **No.** Deferred. The user's brief explicitly excludes it ("skip the dashboard for now" — meaning skip the *frontend dashboard UI*). |
| Real PR creation against GitHub? | **Yes.** User provisions a dedicated test repo with `GITHUB_TOKEN` available. |
| Assert "did the bug get fixed"? | **No.** Strategy A — contractual only. Strategy C deferred to a future test. |
| Run in default CI? | **No.** Opt-in only via marker + env var. Nightly cron deferred until project is funded. |

---

## Risks + mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Claude wanders off / runs long | medium | high cost | 15-min pytest timeout; soft cost warning at $5 |
| GH rate limit during test run | low | test fail | Test repo is dedicated; failures are retryable; no concurrent runs |
| Branch cleanup leaves orphans | low | accumulating noise | Cleanup failures logged but non-fatal; periodic manual sweep |
| HIL stitching races the driver restart | low | test flake | Same pattern as existing fake e2e — proven; helper lifted verbatim |
| Stop-hook validation rejects an artifact Claude wrote | medium | stage FAILED | This is *real coverage* — surface as a bug in either Hammock contracts or the prompt; do not paper over |
| MCPManager startup races first stage | low | stage FAILED | RealStageRunner is expected to await MCP readiness before launching the agent; verify during P1 implementation and add an explicit await if missing |
| Model-pricing change spikes cost | low | warning only | Cost is a warning, not a failure |

---

## Out of scope / explicitly deferred

- Frontend Playwright e2e — separate spec.
- Strategy-C correctness assertion (run fixture's tests post-fix) — separate spec.
- SSE assertions in this test — separate spec.
- `build-feature` template coverage — separate spec.
- Hammock budget-enforcement integration — depends on alignment-drift item #1 shipping.
- Closed-loop HIL → artifact bridge — separate v1+ effort.

---

## Acceptance criteria for this design

The design is accepted when the user has reviewed this written spec and confirmed:

- [ ] Goals + non-goals match intent
- [ ] All design choices reflect the conversation (alternatives + selected + reasoning preserved)
- [ ] Precondition P1 scope is correct
- [ ] No surprises in file layout, gating, or assertion list

Once accepted, the next step is to invoke the `superpowers:writing-plans` skill to produce an implementation plan, broken into:
1. Precondition PR — P1 plumbing fix
2. E2E test PR — the test itself, the shared helpers, the gating, the runner script
