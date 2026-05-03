# Stage 16 — E2E smoke + self-host dogfood

**PR:** _open — pending merge_
**Branch:** `feat/stage-16-e2e-dogfood`

## What was built

The closing stage of v0: a CI-runnable end-to-end test that drives the
full `fix-bug` lifecycle (register → submit → 13 stages → 4 human gates
[3 active, 1 conditionally skipped] → COMPLETED with `summary.md`); a
synthetic dogfood-bug fixture for the manual hammock-on-hammock walk; an
operator runbook; a real README quickstart; and the `e2e` GitHub Actions
workflow that wires the lifecycle test into PR-trigger and nightly cron.

One missing-implementation issue surfaced as the test was written: the
Stage 14 `POST /api/jobs` endpoint never forwarded `Settings.fake_fixtures_dir`
to `spawn_driver`, so the dashboard could not drive a fake-fixture job
end-to-end (only the Stage 15 restart endpoint was correctly plumbed).
The fix is a one-line addition in `dashboard/api/jobs.py` to mirror the
restart path; without it the e2e test fails immediately with the driver
exiting at startup.

### Files added

| File | Purpose |
|---|---|
| `tests/e2e/test_full_lifecycle.py` | The full-lifecycle E2E test. Submits a real `fix-bug` job through `POST /api/jobs`; the dashboard spawns a real `JobDriver` (double-fork grandchild) backed by `FakeStageRunner`; the test polls `job.json` for `BLOCKED_ON_HUMAN`, writes each human-stage's required output artifact + flips `stage.json` to `SUCCEEDED`, re-spawns the driver, and asserts final state `COMPLETED` plus presence of every required artifact (incl. `summary.md` referencing a PR URL). Runs in ~1 s on a workstation. |
| `tests/fixtures/dogfood-bug/` | Synthetic Python micro-project with one intentional off-by-one in `widget.parse_range`. Includes `prompt.md` (the human request), `expected-fix.md` (the recorded ground truth), `pyproject.toml`, `CLAUDE.md`. Used by the manual dogfood walk; *not* by the automated test. |
| `docs/runbook.md` | Operator-facing reference. Sections: install, first run, register a project, submit a job, watching a stage live, answering HILs, common operations (job listing, cancellation, on-disk inspection, doctor, OpenAPI dump), troubleshooting (stuck driver, stale heartbeat, compile failures, validator errors, 404 page), manual dogfood walk-through, where to look next. |
| `.github/workflows/e2e.yml` | GitHub Actions workflow. PR-trigger on backend integration paths; nightly cron at 06:00 UTC; matrix on Python 3.12 + 3.13; 15-minute timeout. Runs `uv run pytest tests/e2e/ -q`. |
| `scripts/manual-smoke-stage16.py` | Local pre-flight smoke that mirrors the e2e test but logs progress to stdout and leaves the tmp hammock-root in place for inspection. Reuses the test's fixture payloads to prevent drift. |
| `docs/stages/stage-16.md` | This file. |

### Files modified

| File | Change |
|---|---|
| `dashboard/api/jobs.py` | `submit_job` now forwards `settings.fake_fixtures_dir` to `spawn_driver`. One-line fix; mirrors the Stage 15 restart endpoint. Without this the dashboard cannot drive a fake-fixture job end-to-end. |
| `pyproject.toml` | `tool.pytest.ini_options.norecursedirs` set explicitly so the dogfood-bug fixture's tests are excluded from hammock's own pytest collection (pytest's default `norecursedirs` is replaced when set, so the full default list is restated alongside `tests/fixtures`). |
| `README.md` | Real install + first-run quickstart (was a Stage 16 placeholder). Points operators at `docs/runbook.md` for everything beyond the four-step happy path. |
| `docs/stages/README.md` | Stage 16 row added. |

## Notable design decisions made during implementation

- **Backend e2e, not Playwright e2e.** The §7 spec calls for a "full
  lifecycle Playwright + backend integration" test. The bulk of v0 critical-
  path correctness lives in the backend file pipeline (compiler →
  spawn_driver → driver state machine → artifacts → state transitions);
  the frontend already has 173 vitest unit tests covering Stage 15. A
  browser e2e adds setup weight (Playwright install, headless Chromium,
  flaky network/timing) for a thin slice the unit tests already cover.
  Stage 16 ships backend e2e only; frontend Playwright is deferred to v1+
  with a backlog item.
- **HIL → artifact bridge is stitched in the test, not in production.**
  The spec calls for "answer HILs"; today, `submit_answer` writes the HIL
  item but does *not* write the stage's required output artifact, mark
  `stage.json` SUCCEEDED, or re-spawn the driver. Closing that loop is a
  v1+ form-pipeline task. The Stage 16 test mimics what the closed-loop
  bridge will produce (output artifact + `stage.json: SUCCEEDED` +
  re-spawn) so the test is forward-compatible: when the bridge ships, the
  test's `_resolve_human_gate` helper can be replaced with a single
  `POST /api/hil/<id>/answer` call.
- **Fake fixtures, not real Claude.** Every agent stage in the e2e test is
  driven by `FakeStageRunner` reading per-stage YAML scripts. This makes
  the test deterministic (no model nondeterminism, no API quota), fast
  (~1 s for 9 agent stages + 3 human gates), and CI-runnable without
  secrets. The trade-off: the test does not exercise the
  `RealStageRunner` path (Stage 5) end-to-end. That coverage stays where
  it already lives — `tests/job_driver/test_real_stage_runner.py` —
  rather than getting duplicated in e2e.
- **Manual dogfood is documented, not automated.** §7's T4 ("orchestrator
  runs the dogfood manually") is intentionally a human task — the goal is
  to discover rough edges that synthetic tests can't surface. The
  fixture lives at `tests/fixtures/dogfood-bug/`; the runbook documents
  the walk; discovered rough edges land as v1+ backlog items in
  `implementation.md § 9`.
- **`norecursedirs` is restated in full.** pytest's `norecursedirs` ini
  option *replaces* its default value when set, rather than extending it.
  Setting it to just `["tests/fixtures"]` would un-exclude `.git`,
  `node_modules`, `venv`, etc. — ruff would still pass but pytest would
  silently start collecting from those directories. The Stage 16 setting
  enumerates the full default list plus `tests/fixtures` so behaviour is
  explicit and future maintainers don't have to know the pytest default.
- **`integration-test-report.json` verdict = passed.** The fix-bug
  template's `review-integration-tests-human` stage has
  `runs_if: "integration-test-report.json.verdict != 'passed'"`. The
  fake fixture sets `verdict=passed` so that human stage is skipped at
  dispatch — which is the realistic happy-path shape (operators only
  intervene on test failures). The list of human gates the test resolves
  is therefore three, not four.

## Locked for downstream stages

- **`Settings.fake_fixtures_dir` propagates from `POST /api/jobs`.** Any
  future endpoint that triggers a driver spawn (e.g., a
  `restart-job` verb) must follow the same pattern. The Stage 15 restart
  endpoint is the precedent; the Stage 16 submit fix completes the
  contract.
- **`tests/e2e/test_full_lifecycle.py::test_fix_bug_full_lifecycle`** is
  the regression sentinel for the full pipeline. Any future change that
  breaks any of: (a) job submission via HTTP, (b) spawn_driver
  detachment, (c) FakeStageRunner fixture loading, (d) JobDriver
  resume-after-block, (e) final-outputs gate before COMPLETED — will
  trip this test. Treat e2e CI failures as load-bearing.
- **`docs/runbook.md`** is the canonical operator reference. Future
  stages adding operator-visible features (CLI verbs, dashboard pages,
  new failure modes) MUST update the relevant runbook section in the
  same PR — same convention as `docs/stages/`.
- **`.github/workflows/e2e.yml`** runs on every PR touching backend or
  templates, plus nightly. Adding a new e2e test file under `tests/e2e/`
  gets it picked up automatically (the workflow runs `tests/e2e/`).

## Files added/modified — full inventory

```
A  .github/workflows/e2e.yml
A  docs/runbook.md
A  docs/stages/stage-16.md
A  scripts/manual-smoke-stage16.py
A  tests/e2e/test_full_lifecycle.py
A  tests/fixtures/dogfood-bug/CLAUDE.md
A  tests/fixtures/dogfood-bug/README.md
A  tests/fixtures/dogfood-bug/expected-fix.md
A  tests/fixtures/dogfood-bug/prompt.md
A  tests/fixtures/dogfood-bug/pyproject.toml
A  tests/fixtures/dogfood-bug/tests/test_parse_range.py
A  tests/fixtures/dogfood-bug/widget/__init__.py
M  README.md
M  docs/stages/README.md
M  dashboard/api/jobs.py
M  pyproject.toml
```

## Dependencies introduced

None. The e2e test reuses `pytest`, `pytest-asyncio`, `fastapi`,
`pyyaml`, and `pydantic` — all already pinned for prior stages. The
manual smoke script reuses the same. The CI workflow adds no new
toolchain (uv + python only).

## Acceptance criteria — status

- [x] **Full lifecycle test runs in CI and passes.** `tests/e2e/test_full_lifecycle.py` runs in ~1 s on a workstation; wired into `e2e.yml` on PR + nightly across Python 3.12 + 3.13.
- [~] **Manual dogfood produces a merge-ready PR — partial.** Procedure documented in `docs/runbook.md § 9`; fixture at `tests/fixtures/dogfood-bug/`; ground-truth fix recorded in the fixture's `expected-fix.md`. **However**, the real-Claude lifecycle blocks on a v1+ wiring gap: `job_driver/__main__.py` requires `--fake-fixtures` and exits 2 without it, and `RealStageRunner` (Stage 5) is not wired into the entry point or `spawn_driver`. The dogfood walk-through demonstrates the operator flow but cannot drive a real fix in v0; it becomes runnable end-to-end once the v1+ runner-selection item ships (now in `implementation.md § 9`).
- [x] **Runbook covers install, first-run, register-project, submit-job, common operations, troubleshooting.** All ten sections in `docs/runbook.md` (1 install, 2 first run, 3 register a project, 4 submit a job, 5 watching live, 6 HIL queue, 7 common ops, 8 troubleshooting, 9 manual dogfood, 10 cross-references). § 4 explicitly calls out the CLI-vs-dashboard distinction (CLI submit does not spawn a driver) and § 4 + § 8 document the real-Claude wiring gap.
- [x] **README quickstart works on a fresh machine.** Quickstart points at the bundled fake-fixture smoke (`scripts/manual-smoke-stage16.py` — verified end-to-end) for the fastest demo, then describes the dashboard-side submit flow for an interactive walk. The previous CLI-submit version of the quickstart was incorrect (CLI submit does not spawn a driver) and was corrected during the Codex review pass.
- [x] **v1+ backlog updated.** Four items added to `docs/implementation.md § 9` and called out as Stage-16-surfaced: (1) closed-loop HIL → artifact bridge in the form pipeline, (2) `RealStageRunner` wired into `job_driver.__main__` + `spawn_driver`, (3) CLI `hammock job submit` optionally spawning the driver, (4) frontend Playwright e2e smoke.

## Notes for downstream stages

- v0 ends here. The `v0.16` tag (per `implementation.md § 8.3`) cuts the
  release line; v1+ stages continue at Stage 17 with new tags `v1.*`.
- The first v1+ stage will most likely be the **HIL → artifact bridge in
  the form pipeline**: it closes the only loop that Stage 16 had to
  stitch by hand, and it's the highest-leverage operator-experience win
  (no more "manually edit `stage.json` after answering"). The Stage 16
  e2e test's `_resolve_human_gate` helper documents the on-disk shape
  the bridge must produce — implementing the bridge is mostly a matter
  of moving that helper's logic into a new `dashboard/hil/bridge.py`
  invoked from the form-submit endpoint.
- A second v1+ candidate is **frontend Playwright smoke**: a single
  `tests/e2e-frontend/` spec that drives the dashboard through a
  similarly-scripted lifecycle. Worth pairing with the HIL bridge so
  the operator-flow test doesn't need to write `stage.json` from
  Playwright code.
- The `RealStageRunner` exists (Stage 5) but is not wired into
  `python -m job_driver` — the entry script still requires
  `--fake-fixtures` and exits 2 without it. Any v1+ stage that turns on
  real Claude will need to (a) wire the runner-selection flag, (b)
  surface it as a `Settings` field, and (c) make sure the e2e test
  switches to `FakeStageRunner` explicitly so CI never hits real Claude.

## Verify subsection

```text
$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
158 files already formatted

$ uv run pyright shared/ dashboard/
0 errors, 0 warnings, 0 informations

$ uv run pytest tests/ -q
... 631 passed in ~15s

$ uv run python scripts/manual-smoke-stage16.py
[smoke] tmp root: /tmp/hammock-stage16-smoke-...
[smoke] registered project: smoke-target
[smoke] submitted: <yyyy-mm-dd>-smoke-off-by-one
[smoke] reached BLOCKED_ON_HUMAN (human stage: review-design-spec-human)
[smoke] resolved review-design-spec-human + re-spawned driver
[smoke] reached BLOCKED_ON_HUMAN (human stage: review-impl-spec-human)
[smoke] resolved review-impl-spec-human + re-spawned driver
[smoke] reached BLOCKED_ON_HUMAN (human stage: review-impl-plan-spec-human)
[smoke] resolved review-impl-plan-spec-human + re-spawned driver
[smoke] PASS — job <yyyy-mm-dd>-smoke-off-by-one reached COMPLETED
```
