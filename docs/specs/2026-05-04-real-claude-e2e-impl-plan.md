# Real-Claude E2E Test — Implementation Plan

**Status:** proposed
**Date:** 2026-05-04
**Source design:** `docs/specs/2026-05-03-real-claude-e2e-test-design.md` (preconditions complete)

This plan covers the single closing PR — `feat(e2e): real-claude lifecycle test (closing PR of precondition track)`. Not split, because the components only earn their keep when the test itself runs; the helpers without the test are dead code.

---

## Component map

| Step | What | Where | TDD-able? |
|---|---|---|---|
| A | Dev-deps + marker | `pyproject.toml` | n/a (mechanical) |
| B | Seed repo content | `tests/e2e/seed_test_repo/` | n/a (static) |
| C | Bootstrap helpers | `tests/e2e/test_repo_bootstrap.py` | yes |
| D | Preflight fixture | `tests/e2e/preflight.py` | yes |
| E | Fixture-builder registry | `tests/e2e/hil_builders.py` | yes |
| F | HIL stitching helper | `tests/e2e/hil_stitcher.py` | yes (extracted from existing fake e2e) |
| G | Cleanup helper | `tests/e2e/cleanup.py` | yes |
| H | Outcome assertion helpers | `tests/e2e/outcomes.py` | yes |
| I | The test itself | `tests/e2e/test_real_claude_lifecycle.py` | smoke only; real exercise gated on opt-in |
| J | Verify + Codex + push | — | — |

Sequence: A → B → (C, E, F, G, H in parallel) → D → I → J. C/E/F/G/H are independent; bunch them in the order the reviewer reads them.

---

## Step A — Dev-deps + pytest marker

**Goal.** Register `pytest-timeout` and the `real_claude` marker so the test can both opt in and self-cap on wall-clock.

**Files.**
- `pyproject.toml` — add `pytest-timeout>=2.3` to `[dependency-groups].dev`; add `real_claude` to `[tool.pytest.ini_options].markers`.

**Steps.**
1. Add the dependency entry. Run `uv lock` so the lockfile reflects it.
2. Register the marker.
3. Confirm `uv run pytest --collect-only -q` doesn't gripe.

**Done when**
- `pytest-timeout` resolves on `uv sync`.
- A throwaway `@pytest.mark.real_claude` on a no-op test doesn't trigger `--strict-markers` failure.

---

## Step B — Seed test repo content

**Goal.** A small, credible Python program that fix-bug and build-feature can both act on.

**Files.**
- `tests/e2e/seed_test_repo/` — pushed verbatim to the test repo's `main` on first creation:
  - `add_integers.py` — `def add_integers(*nums: int) -> int: return sum(nums)`.
  - `test_add_integers.py` — pytest covering the function (one passing, one parametrised).
  - `README.md` — three lines: title, purpose, "run `pytest` to test."
  - `pyproject.toml` (minimal) — `[project]` with name `e2e-test-repo`, optional dev-dep `pytest`.
  - `.gitignore` — `__pycache__/`, `.pytest_cache/`, `*.pyc`.

**Steps.**
1. Author the four files above. Keep them tiny — under 30 lines each.
2. Verify by hand: `cd tests/e2e/seed_test_repo && uv run --with pytest pytest` passes.

**Risks.** None of substance. Don't over-engineer the seed.

**Done when** the directory exists + pytest passes inside it.

---

## Step C — Bootstrap helpers

**Goal.** Pure functions over `gh` + `git` that create-or-reuse the test repo and enable branch protection on first creation.

**Files.**
- `tests/e2e/test_repo_bootstrap.py` (production code, despite the `test_` prefix matching the e2e package style — see `tests/e2e/test_full_lifecycle.py` for precedent of co-locating helpers in a single file). Or simply `tests/e2e/repo_bootstrap.py` if cleaner — pick during impl.
- `tests/e2e/test_e2e_repo_bootstrap.py` (unit tests).

**Surface.**
- `class RepoBootstrapResult: created: bool, repo_url: str, default_branch: str`.
- `def bootstrap_test_repo(repo_url: str, *, seed_dir: Path, gh: GhRunner | None = None) -> RepoBootstrapResult`:
  1. `gh repo view <repo>` — capture exit code and stderr.
  2. If "not found" → `gh repo create <repo> --private --description "Hammock e2e test repo"`, then clone, copy `seed_dir/*` over (excluding `.git`), `git add . && git commit -m "seed"`, `git push -u origin main`, then `gh api -X PUT repos/<repo>/branches/main/protection -f required_pull_request_reviews.required_approving_review_count=1 -f enforce_admins=false -f restrictions=null` (or equivalent). Return `created=True`.
  3. If exists → return `created=False`.
  4. Any other error (auth denied, network) → raise `RepoBootstrapError`.

**TDD steps.**
1. RED — `test_bootstrap_creates_when_absent`: mock `GhRunner` to return "not found," assert `gh repo create` + push + protection-API calls happen in order.
2. RED — `test_bootstrap_reuses_when_present`: mock `gh repo view` to succeed, assert no create/push.
3. RED — `test_bootstrap_raises_on_auth_error`: mock `gh repo view` returning auth-denied, assert raises.
4. GREEN — implement.
5. RED — `test_bootstrap_seed_push_only_to_main`: assert pushes are `git push origin main` (not other branches), via stubbed `git`.
6. RED — `test_bootstrap_protection_payload_shape`: pin the protection-API payload (dict shape) so future refactors don't silently weaken parity.

**Risks.**
- `gh` doesn't have a structured "not found" exit code; will need to grep stderr (`Could not resolve to a Repository`). Pin the matcher and write a test for it.
- Branch protection requires admin on the repo. The bootstrap path runs as the repo *creator* (so admin by definition); subsequent runs don't touch protection.

**Done when** all 6 unit tests green; `bootstrap_test_repo` is the only function the e2e test calls.

---

## Step D — Preflight fixture

**Goal.** Run all preflight checks, applying the skip-vs-fail policy from D12.

**Files.**
- `tests/e2e/preflight.py` (helper).
- `tests/e2e/test_preflight.py` (unit tests).

**Surface.**
- `def run_preflight(*, env: Mapping[str, str], gh: GhRunner) -> None`. Returns on success; calls `pytest.skip` on opt-in-not-set; raises `PreflightFailure` otherwise (which the test re-raises as `pytest.fail` with a clear message).

**Checks (per spec §Preflight checks).**
1. `HAMMOCK_E2E_REAL_CLAUDE != "1"` → **skip**.
2. Beyond this point, every miss is **fail**:
   - `HAMMOCK_E2E_TEST_REPO_URL` unset.
   - `HAMMOCK_E2E_JOB_TYPE` unset.
   - `git --version` non-zero.
   - `gh auth status` non-zero.
   - `gh repo view` fails for a reason other than not-found (parsed by stderr).
   - `claude --help` non-zero, OR doesn't list the expected flags.
   - `python -c "import dashboard.mcp"` non-zero (MCP module importable).
   - Network probe: `curl -fsS https://api.github.com -o /dev/null -m 5`.

**TDD steps.**
1. RED for skip-on-opt-in-unset; RED for each fail case (one test per check).
2. GREEN — implement.
3. RED for the happy path: all env vars set, all subprocesses succeed → returns silently.
4. NIT: parameterise the per-check tests so adding a check is one entry.

**Risks.**
- Subprocess mocking is finicky. Use a simple `GhRunner` / `CmdRunner` interface so the tests can inject a stub.
- Detecting claude flag support is fragile; spec says "doesn't support the required flags" — interpret loosely: `claude --help | grep -q -- --output-format` is enough for v0.

**Done when** every preflight branch has one unit test and `run_preflight` is pure (no module-level state).

---

## Step E — Fixture-builder registry

**Goal.** A plain dict mapping artifact schema names to "what would a sane operator write here?" payload builders.

**Files.**
- `tests/e2e/hil_builders.py` (helper).
- `tests/e2e/test_hil_builders.py` (unit tests).

**Surface.**
- `BUILDERS: dict[str, Callable[[BuilderContext], bytes]]`.
- `BuilderContext` — small dataclass with `job_dir: Path`, `stage_id: str`, `output_path: str`, `schema: str`.
- `def build(schema: str, ctx: BuilderContext) -> bytes`. Raises `MissingBuilderError(schema)` if not registered.

**Initial entries.** Walk `dashboard/artifact_validators/` (or wherever the registered validators live) to enumerate schemas the production templates declare:
- `non-empty` → returns `b"placeholder content\n"`.
- `review-verdict-schema` → returns approved-verdict JSON.
- `plan-schema` → returns minimal plan JSON.
- `pr-merge-form` (and other UI form schemas) → returns minimal form payload.

The exhaustive list is derived from running `uv run python -c "from dashboard.artifact_validators import REGISTRY; print(REGISTRY.keys())"` (or equivalent) and adding one builder per schema.

**TDD steps.**
1. RED — `test_missing_builder_raises_named_error`: assert `MissingBuilderError("foo-schema")` for unknown.
2. RED — `test_each_registered_builder_returns_bytes`: parametrised over all entries.
3. RED — `test_each_builder_validates_against_its_schema`: feed the output through the production validator registry; must pass.
4. GREEN — implement entries one by one until #3 passes for every entry.

**Risks.**
- Builder output that doesn't validate would defeat the purpose. The #3 test is the lock-down.
- New templates → new schemas → missing builder. The registry's own error message names the schema; adding is one entry.

**Done when** every schema referenced by the bundled job templates has a registered builder, and #3 passes for all of them.

---

## Step F — HIL stitching helper

**Goal.** Extract the gate-resolution logic from the existing `tests/e2e/test_full_lifecycle.py` so both the fake e2e and the real-claude e2e share one helper.

**Files.**
- `tests/e2e/hil_stitcher.py` (new helper).
- `tests/e2e/test_hil_stitcher.py` (unit tests).
- `tests/e2e/test_full_lifecycle.py` (refactored to call the helper).

**Surface.**
- `async def stitch_hil_gate(*, root: Path, job_slug: str, app_client: TestClient, builders: BuildersRegistry) -> StitchResult`:
  1. Find the BLOCKED_ON_HUMAN stage (read `stage.json` files under the job dir).
  2. Resolve required output schemas from the compiled stage list.
  3. For each required output: build via the registry, write to disk.
  4. POST `/api/hil/{id}/answer` (P5 made this work).
  5. Return `StitchResult(stage_id, item_id, written_paths)`.

**TDD steps.**
1. RED — extract one of the existing fake-e2e gate-stitching scenarios into a helper test that calls `stitch_hil_gate` directly. It currently fails because the helper doesn't exist.
2. GREEN — extract the logic from `test_full_lifecycle.py` verbatim into the helper, with parameter substitution.
3. REFACTOR — `test_full_lifecycle.py` calls the helper; existing test still passes.
4. RED — `test_stitch_missing_builder_raises_named_error` (uses Step E's MissingBuilderError).
5. RED — `test_stitch_calls_answer_endpoint_after_disk_write` (assert ordering — disk first, then HTTP, so the answer endpoint always sees an item that exists in the cache).

**Risks.**
- The existing fake e2e's gate-stitching is complex; do not change semantics, just relocate. Run the full fake e2e suite after the refactor as the regression guard.

**Done when** the existing fake e2e is ≤ green via the new helper and 5 new helper tests pass.

---

## Step G — Cleanup helper

**Goal.** Single fixture teardown that runs unconditionally, logs cost, deletes branches the run created, removes the tmp root unless preserved.

**Files.**
- `tests/e2e/cleanup.py` (helper).
- `tests/e2e/test_cleanup.py` (unit tests).

**Surface.**
- `class RunSnapshot: pre_branches: set[str]`.
- `def take_snapshot(repo_url: str, *, gh: GhRunner) -> RunSnapshot`.
- `def teardown(*, root: Path, repo_url: str, snapshot: RunSnapshot, keep_root: bool, gh: GhRunner) -> None`:
  1. Best-effort: read `<root>/jobs/<slug>/cost_summary.json`, log "Run cost: $X" or "(no cost summary)".
  2. List remote branches; compute `current - snapshot.pre_branches`.
  3. For each new branch: `git push origin --delete <branch>` (best-effort, log on failure).
  4. If not `keep_root`: `shutil.rmtree(root, ignore_errors=False)` (no `True` — we want failures visible).

**TDD steps.**
1. RED — `test_teardown_deletes_only_new_branches`: snapshot 2 pre-existing, run "creates" 3, assert exactly the 3 new are deleted.
2. RED — `test_teardown_logs_cost_summary`: write a fake cost_summary.json, assert log line contains "$0.4216" (whatever).
3. RED — `test_teardown_logs_when_cost_summary_missing`: no file, assert "(no cost summary)" log.
4. RED — `test_teardown_preserves_root_when_flag_set`: assert dir still exists.
5. RED — `test_teardown_continues_on_branch_delete_failure`: stub one delete to fail; assert others still happen + log.

**Risks.**
- `git push --delete` failures are common (race with concurrent operations on the repo). Best-effort + log; never raise.
- `shutil.rmtree` on busy file handles (open log files) can fail on some platforms. Spec says "log + continue."

**Done when** all 5 unit tests green.

---

## Step H — Outcome assertion helpers

**Goal.** One function per outcome, each pure-on-disk, named so the test reads as a contract spec.

**Files.**
- `tests/e2e/outcomes.py`.
- `tests/e2e/test_outcomes.py` (unit tests covering each helper against synthetic job dirs).

**Surface.**
```python
def assert_job_completed(root, job_slug) -> None
def assert_all_stages_succeeded(root, job_slug) -> None
def assert_no_failed_or_cancelled(root, job_slug) -> None
def assert_required_outputs_exist(root, job_slug) -> None
def assert_stop_hook_fired_for_each_succeeded_stage(root, job_slug) -> None
def assert_summary_md_has_url(root, job_slug) -> None
def assert_agent_artifacts_present(root, job_slug) -> None  # stream/messages/result/stderr × every agent stage
def assert_branches_exist(repo_url, *, gh, job_slug) -> None
def assert_event_stream_well_formed(root, job_slug) -> None
def assert_worker_exit_for_each_succeeded_stage(root, job_slug) -> None  # exit_code=0 + succeeded=True
def assert_at_least_one_worktree_created_event(root, job_slug) -> None
```

Each takes the minimum it needs; failures raise `AssertionError` with a message naming the missing piece + the source-of-truth path.

**TDD steps.** For each helper:
1. RED — synthetic green job dir; helper passes.
2. RED — synthetic broken job dir (one specific outcome violated); helper raises with named-piece message.
3. GREEN — implement.

**Risks.** Test-file synthesis can drift from real production shapes. Use the existing `tests/job_driver/test_runner.py` factories where possible to stay aligned.

**Done when** every outcome (#1–#14 from spec, minus #11 which depends on GITHUB_TOKEN being plumbed; that one stays in the test but may xfail until project-config lands) has a helper + 2 unit tests.

---

## Step I — The test itself

**Goal.** A single test function that wires steps C–H into the lifecycle the spec describes.

**Files.**
- `tests/e2e/test_real_claude_lifecycle.py`.

**Shape.**

```python
@pytest.mark.real_claude
@pytest.mark.timeout(int(os.environ.get("HAMMOCK_E2E_TIMEOUT_MIN", "30")) * 60)
async def test_real_claude_full_lifecycle(tmp_path):
    cfg = run_preflight(env=os.environ, gh=GhRunner())
    # cfg holds: repo_url, job_type, claude_binary, keep_root

    bootstrap = bootstrap_test_repo(cfg.repo_url, seed_dir=SEED_DIR)
    snapshot = take_snapshot(cfg.repo_url, gh=GhRunner())

    root = tmp_path / "hammock-root"
    project_slug = register_project(root, cfg.repo_url)
    job_slug = submit_job_via_cli(root, project_slug, cfg.job_type)

    settings = Settings(root=root, run_background_tasks=False)
    with TestClient(create_app(settings)) as app_client:
        await drive_to_terminal(
            root=root,
            job_slug=job_slug,
            app_client=app_client,
            builders=BUILDERS,
        )
        # Assertions — call each helper from Step H
        assert_job_completed(root, job_slug)
        assert_all_stages_succeeded(root, job_slug)
        # ... etc
```

`drive_to_terminal` is the polling loop:
- Read `job.json`. If COMPLETED/FAILED/ABANDONED → return.
- If BLOCKED_ON_HUMAN → call `stitch_hil_gate` (Step F), then re-spawn the driver via `spawn_driver`.
- Sleep 1s, repeat.
- Wall-clock cap is enforced by `pytest-timeout`; no per-iteration timeout in the helper.

**TDD steps.** The test itself isn't TDD — it's the integration target. Smoke check during dev: run with `HAMMOCK_E2E_REAL_CLAUDE=` (unset) and confirm `pytest.skip` fires cleanly via the CI path.

**Risks.**
- Driver respawn timing — spec acknowledges this in §Risks. The 1 s polling + driver respawn is the same pattern the existing fake e2e uses; v0 ships this and tightens later if flakes appear.
- A test that doesn't actually run by default is hard to keep healthy; the unit tests in Steps C–H carry most of the regression coverage.

**Done when** the file exists, the test skips cleanly with opt-in unset, and a manual real-mode run completes (operator side).

---

## Step J — Verify + Codex + push

Standard workflow:

1. `uv run ruff check . && uv run ruff format . && uv run pyright && uv run pytest tests/` — all green.
2. Commit per step (A–I) with `feat(e2e):` / `test(e2e):` prefixes.
3. Push branch `feat/real-claude-e2e-test`.
4. Open PR, take Codex review (`Agent(subagent_type="codex:codex-rescue")`).
5. Address findings in a follow-up commit.
6. Hand back to user for merge.

---

## Cross-cutting concerns

**Test conventions.** TDD per existing patterns: RED → GREEN → REFACTOR with a verify-fail-first check before claiming green.

**Subprocess interfaces.** Steps C, D, G all shell out to `gh` / `git` / `claude`. Define a single `CmdRunner` (or two: `GhRunner`, `GitRunner`) so unit tests inject stubs without monkey-patching `subprocess.run`. The interfaces stay tiny — `run(args: list[str]) -> CompletedProcess`.

**No new MCP / driver / dashboard changes.** Everything in this PR is under `tests/e2e/` plus `pyproject.toml`. If anything outside that scope creeps in, it's a sign the precondition track was incomplete.

**Cost discipline.** During development run with fake-fixtures wiring; only flip the real-claude env var on the final integration smoke. The unit tests in Steps C–H must NOT call real claude.

---

## Resolved decisions

1. **Repo identity.** `HAMMOCK_E2E_TEST_REPO_URL` is *optional*; default is `https://github.com/<gh-user>/hammock-e2e-test` derived from `gh api user --jq .login`. The test creates-or-reuses regardless of which form supplied the URL.
2. **Outcome #11.** Hard assertion. GITHUB_TOKEN plumbing into spawned claude is a Hammock-wide project-config concern, not test-specific; the test failing on this is the correct signal that the project-config flow needs work. No `xfail`.
3. **Validator registry.** Will grep `dashboard/artifact_validators/` (or wherever) during Step E. If named differently, follow the imports from the existing fake e2e's HIL stitching.

---

## Sequencing reminder

A → B → (C, E, F, G, H concurrent) → D → I → J. Two reviewable streams (steps A/B/C/D/I and steps E/F/G/H) but a single PR.
