# Stage 12.5 — Audit remediation

**Branch:** `feat/stage-12.5-audit-remediation`
**Worktree:** `~/workspace/hammock-stage-12.5`
**Status:** plan

## Why this stage exists

Stages 0–12 shipped feature-by-feature with per-stage TDD and Codex review. Now that the read surface is end-to-end (Stage 12), we have enough vantage to look back across all twelve stages, compare each against `docs/design.md` / `docs/implementation.md` / `docs/presentation-plane.md` / `docs/02-proposal-lifecycle.md` / `docs/hil-bridge-mcp-section.md`, and clean up the residue before Stage 13 starts adding mutations (form submissions, HIL answers, job-control endpoints).

This is an **audit-driven cleanup stage**, not a feature stage. No new design surface. The goal is to land a remediation pass with the same TDD + Codex review discipline as a feature stage, then move on.

## Source of findings

Findings are the consolidated output of:

1. Four parallel sub-audits (stages 0–4, 5–8, 9–12, plus a cross-cutting code-quality scan) on `main` at commit `8976555`.
2. A Codex review of the resulting plan, with section-anchored access to `design.md` / `implementation.md` / `presentation-plane.md` / `hil-bridge-mcp-section.md`.
3. Direct `Read`-based verification of every P0 / P1 claim and every Codex disagreement before this revision.

Several findings from the first audit pass turned out to be wrong on closer reading; those have been removed. Several findings the audit missed but Codex surfaced or hinted at have been added. The list below is the *verified* set.

Findings cite `path:line` against commit `8976555`. Re-verify before fixing — line numbers drift.

---

## Scope

### In scope

- Land all P0 fixes (correctness / silent data loss).
- Land most P1 fixes (silent error swallowing, contract drift, spec divergence in docs vs code).
- A bounded, named subset of P2 fixes (test gaps for code that already exists, plus low-effort polish).

### Out of scope (explicitly deferred)

- Schema versioning / migration layer for persisted models (revisit when first model field actually changes).
- Property-test coverage for `shared/models/*` (the models are exercised through every integration test — adding 11 dedicated unit suites is bulk for low marginal value at this stage).
- Pyright path widening — `.github/workflows/backend.yml:65` runs pyright on `shared/ dashboard/` only. Adding `job_driver/`, `cli/`, `tests/`, `scripts/` is good hygiene but out of scope for 12.5; do it as a separate PR.
- Auth / authz hooks on SSE / API routes (Stage 16+ per design).
- Any new feature surface — no new endpoints, views, or models.

---

## Findings consolidated

Severity legend: **P0** — silent data loss / silent correctness failure. **P1** — silent error swallowing or contract drift that masks real bugs. **P2** — polish, test gaps, low-effort hygiene.

### A. Backend correctness

| ID  | Sev | Where | Issue | Fix |
|-----|-----|-------|-------|-----|
| A1  | P0  | `dashboard/state/pubsub.py:183-189` | Malformed `stage:` scope (no second colon) silently returns empty path list, so `replay_since` yields nothing. Same fall-through at line 189 for any unknown scope shape. SSE 200s with empty replay. **Existing tests `tests/dashboard/state/test_pubsub_replay.py:156-163` actively codify this wrong behaviour** — they assert the empty-list outcome. | `_jsonl_paths_for_scope` raises `ValueError` on malformed/unknown scope. SSE handler in `dashboard/api/sse.py` maps that to 422. Update the two existing tests to assert the new behaviour, plus the new cases under E3 below. |
| A2  | P0  | `shared/models/stage.py` (`ArtifactValidator`) + `job_driver/runner.py` post-success path; `dashboard/compiler/validators.py:34-44` | `StageDefinition.artifact_validators` and `RequiredOutput.validators` are part of the contract but never enforced at runtime. A stage can declare `validators: ["non-empty"]` on an output and write `{}` and pass. Compile-time check at `validators.py` doesn't validate names against any registry either, so unknown validator names silently pass too. | Wire a registry of named validators (`non-empty`, `review-verdict-schema`, etc.) keyed by string. Compiler rejects unknown names (fail-closed). Job Driver runs them in `_run_single_stage` after `required_outputs` existence check. If we genuinely intend to defer schema enforcement past v0, then remove the field from the model so the contract surface is honest — current state misleads. |
| A3  | P0  | `job_driver/runner.py:405` (driver) **and** `dashboard/state/projections.py:238,241` (projection) **and** `docs/design.md:2741,2950` (spec) | Three-way mismatch on the `cost_accrued` event payload shape. **Spec** says `{delta_usd, delta_tokens, running_total}`. **Driver** writes `{amount_usd: <float>}`. **Projection** reads `payload.get("usd")` and `payload.get("tokens")`. The result is that `_fold_cost_events` returns `0.0` for every job, then `_job_total_cost` falls back to `sum(stage.cost_accrued)` from the cache (`projections.py:212`), which silently masks the bug — but per-job cost rollups, by-stage cost breakdowns, and by-agent breakdowns from `events.jsonl` are all returning zeroes. Cost dashboard shows `total_usd: 0` for completed jobs whose stage cache happens not to be populated. | Pick one shape, write it, read it. Recommend the spec shape: payload `{delta_usd, delta_tokens, running_total}`. Update driver, projection, fold, and any tests in lockstep. Add a regression test that runs a fake stage, asserts the event payload key names, and asserts the projection reads them. |
| A4  | P0  | `dashboard/api/sse.py:65-66` (backend) + `dashboard/frontend/src/sse.ts:49-57` (frontend) | Live SSE messages are emitted with a named event type (`event: project_changed\ndata: …`). Per the HTML EventSource spec, named events fire **only** `addEventListener("project_changed", …)`, not `onmessage`. Frontend only registers `source.onmessage`. **Result: every live cache change is silently dropped by the browser.** Replay events (which omit the `event:` line) work; live updates do not — exactly the path the read-views were supposed to lean on. The accompanying typing problem (`SseEvent` typed only for the replay shape; live `CacheChange` has different fields) is the second-order issue. | Pick one of: (a) drop the `event:` line in `_format_change`, send live changes as unnamed messages, frontend keeps `onmessage`, frontend narrows the union; or (b) keep named events, frontend uses `addEventListener` for each kind. (a) is smaller and less invasive — recommend it. Make `SseEvent` a discriminated union (`ReplaySseEvent` vs `LiveSseEvent`) so callers narrow before reading `seq` etc. Vitest must cover both shapes round-tripping through a real `EventSource` mock. |
| A5  | P1  | `dashboard/api/sse.py:142-150` + `dashboard/state/cache.py:80-88` + `dashboard/watcher/tailer.py:149-150` | Live SSE *log-event* delivery (tailing `events.jsonl`) is not wired. `cache.classify_path` returns `"unknown"` for `events.jsonl` paths (it only classifies state JSON), the watcher skips unknowns, and `sse.py:147-150` carries a NOTE "deferred to Stage 11" — but Stage 11 is merged and didn't pick this up. So the live phase only carries `CacheChange` (state-file mutations), never typed `Event` log entries. Replay works (it reads JSONL directly); live tail does not. | Wire live event-log tail. Add `events_jsonl` (job-scoped) and `events_jsonl_stage` (stage-scoped) classifications to `classify_path`. The watcher tails appends and publishes typed `Event` records to the appropriate scope (`job:<slug>`, `stage:<job>:<sid>`, `global`) on a separate pubsub channel from `CacheChange`. SSE live phase consumes both channels and serialises `Event` with `id: <seq>` (so reconnect Last-Event-ID works), `CacheChange` without (its data isn't replayable). Watcher reads only the tail (track per-file byte offset; on restart resume from offset, skip torn-tail bytes). Test: write three events, confirm all three reach a connected SSE client; restart the dashboard, write a fourth, confirm reconnect Last-Event-ID resumes from 4 with no duplicates. |
| A6  | P1  | `job_driver/runner.py:174-184` and `:265-270` | Predicate evaluation policies on `PredicateError` are **asymmetric**: `runs_if` defaults to **True** (skip-on-error becomes run-on-error) and `loop_back.condition` defaults to **False** (loop-on-error becomes don't-loop). Neither is necessarily wrong, but the asymmetry is undocumented and likely accidental. The first audit pass got this wrong by claiming both default to True; verification at `runner.py:270` confirms `condition_holds = False`. | Pick a unified policy and document it in `docs/design.md` § Plan Compiler / Predicate grammar. Recommend: both default to the **safer side** (skip-runs_if=False; don't-loop=False). Either way, log at `error` (not `warning`) so a runtime predicate error is visible — a predicate that compiled but blew up at evaluation is a real bug somewhere upstream. |
| A7  | P1  | `job_driver/runner.py:468-471`, `:657-660`, `:684-687` | Three `except Exception:` blocks in `_is_stage_succeeded`, `_block_on_human`, `_fail_stage`. Catching `Exception` is too broad and masks `OSError` from disk corruption, `ValidationError` from schema drift, etc. Behaviour is probably correct (treat unreadable as not-yet-succeeded; treat unreadable existing as None) but the cause is invisible. | Narrow to `(json.JSONDecodeError, ValidationError, OSError)`, log at `warning` with file path + error, keep the existing return value. |
| A8  | P1  | `dashboard/api/sse.py:80-102` (replay), `dashboard/state/pubsub.py:172-180` (per-job iteration) | On global scope, `_format_replay_event` correctly suppresses `id:` so the browser never stores a Last-Event-ID. But `_event_stream:142` still accepts a `Last-Event-ID` header on global scope and applies its value as a per-job filter (`event.seq > last_event_id`). A client that sends `Last-Event-ID: 100` on `/sse/global` will silently drop every event from any job whose local seq is below 100 — only those from jobs whose own seq exceeded 100 get replayed. | On global scope, ignore `Last-Event-ID` (treat as `None`). Add a test that asserts replay yields events from low-seq jobs even when Last-Event-ID is high. |

### B. Frontend correctness

| ID  | Sev | Where | Issue | Fix |
|-----|-----|-------|-------|-----|
| B1  | P1  | `dashboard/frontend/src/views/CostDashboard.vue:14-23` + `dashboard/frontend/src/api/queries.ts:83-89` | Scope `<select>` only offers `job` and `project`. Backend supports `stage` (with required `?job=` param). Even adding the option doesn't work in isolation — `useCosts(scope, id)` only takes two args; backend will 422 without `job`. | Add `<option value="stage">`. When selected, render a second input for the job slug. Extend `useCosts` signature to `useCosts(scope, id, job?)` and pass `&job=<slug>` when present. Vitest covers stage scope round-trip. |

### C. Spec divergences (docs vs code)

These are **not code blockers** — current source-of-truth design docs broadly match the code. They are doc-edit chores to remove ambiguity that would mislead the next contributor.

| ID  | Sev | Where | Fix |
|-----|-----|-------|-----|
| C1  | P2  | `docs/design.md` § Job Driver / events vs `docs/stages/stage-04.md` and `stage-05.md` | Stage summaries paint a slightly fuzzy picture of who first wrote `events.jsonl`. Implementation matches design (Job Driver writes structured events). Edit `design.md` to make the owner explicit, by component name, not stage number. Stage summaries stay as historical records. |
| C2  | P2  | `docs/design.md` § HIL nudges + `docs/hil-bridge-mcp-section.md` vs `docs/stages/stage-05.md` and `stage-06.md` | Stage docs disagree on which side delivers the nudge. Design + hil-bridge doc currently name MCP correctly. Edit `design.md` HIL § so the named-component story is reinforced and not contradicted by drive-by stage-doc edits later. |
| C3  | P1  | `dashboard/frontend/src/api/schema.d.ts` (hand-authored) + `dashboard/frontend/package.json:17` (`schema:sync` requires running backend) | `schema.d.ts` drifts from the backend OpenAPI doc. Stage 12 hit a `cost_usd` → `total_cost_usd` near-miss. **Preferred fix:** generate `schema.d.ts` from `/openapi.json` in CI and fail PR on diff. **Acceptable smaller fix for 12.5:** a single pytest that boots the FastAPI app in-process, dumps `app.openapi()`, normalises (sort keys), and asserts a hash matches a checked-in fixture; refresh fixture deliberately. Pick the smaller fix for 12.5 unless the larger one fits comfortably in scope. |
| C4  | P1  | `docs/design.md` § Stage primitive (around L2110) + `dashboard/compiler/validators.py:113-140` + `tests/dashboard/compiler/test_validators.py` | **Policy decision: v0 does not support parallel stage execution.** The Job Driver runs stages strictly sequentially (`runner.py:166-167`), `parallel_with` is currently honoured nowhere at runtime, and v0 templates don't use it. Codifying the policy: (1) no stage definition — template *or* expander-generated — may set `parallel_with`. (2) The field stays on the model as a kernel primitive reserved for v1+. (3) Expander stages must not introduce `parallel_with` into `plan.yaml`. | Update `docs/design.md` § Stage primitive to add an explicit "v0 parallelism" subsection stating the above. Replace `validate_parallel_with` with a check that fails compilation if any stage has a non-null `parallel_with`, with error message "parallel_with is reserved for v1+; v0 runs stages sequentially". Update existing tests in `test_validators.py` that exercised the old symmetry check — the field is now compile-rejected, so those tests assert rejection. No template / yaml file changes needed (none use it). |

### D. Test infrastructure / dev environment

| ID  | Sev | Where | Issue | Fix |
|-----|-----|-------|-------|-----|
| D1  | P2  | `README.md`, `tests/shared/test_paths.py:7`, `tests/shared/test_slug.py:6` | A fresh clone running `uv sync` (no flags) and `uv run pytest` fails collection on these two files because `hypothesis` is in `[dev]` only. **CI is fine** — `.github/workflows/backend.yml:56` uses `uv sync --dev`. So this is a contributor-onboarding paper cut, not a CI gap. | Add a "Setting up" section to `README.md` documenting `uv sync --dev` (or `--all-groups`). Optionally a `Makefile` `bootstrap` target. |
| D2  | P2  | `dashboard/compiler/overrides.py`, `dashboard/settings.py` | Indirectly covered by integration tests. `overrides.py` has merge logic that's easy to drift. | Add `tests/dashboard/compiler/test_overrides.py` and `tests/dashboard/test_settings.py`. Cover obvious cases only; don't gold-plate. |

### E. Test gaps

| ID  | Sev | Where | Issue | Fix |
|-----|-----|-------|-------|-----|
| E1  | P2  | `tests/dashboard/api/test_sse.py` | A1 exists *because* nothing tests these scope shapes end-to-end via the API. Existing `tests/dashboard/state/test_pubsub_replay.py:156-163` codifies the wrong outcome at the projection layer; we need both layers fixed. | Tests for `stage:` (no second colon), `unknown:foo`, empty string, `stage::sid` (empty job), `stage:job:` (empty sid), `project:slug` (recognised v1+ but not v0). All should 422 at the SSE route. |
| E2  | P2  | `tests/dashboard/compiler/test_compile.py` | No test that compiles a plan with `loop_back.max_iterations` and asserts persistence into `Plan.stages[i].loop_back`. | One test, one assertion. |
| E3  | P2  | `tests/cli/test_doctor.py` | `doctor` doesn't validate that `repo_path` exists / is a git repo. | Add the check + the test. |
| E4  | P2  | `tests/job_driver/test_runner.py` | A torn-tail test exists at `:700-731` (`test_event_seq_tolerates_truncated_tail`). What's missing is an end-to-end test: write a torn tail, **restart the driver**, run a fake stage, assert the next emitted event has `seq = max(valid_seqs) + 1` and lands on disk with no duplicate. | Promote the existing test to "seq cursor at startup" and add the restart-and-emit variant. |

### F. Polish

| ID  | Sev | Where | Fix |
|-----|-----|-------|-----|
| F1  | P1  | `job_driver/runner.py` `on_exhaustion` branch | Loop-back exhaustion writes `BLOCKED_ON_HUMAN` but `OnExhaustion.prompt` is never written into a `HilItem`, so the dashboard HIL queue carries no prompt — the dashboard user sees a blocked job with no question. Per `docs/hil-bridge-mcp-section.md:106-120`, manual-step is the prescribed mechanism. | When exhaustion fires, also create a `HilItem(kind="manual-step", question=ManualStepQuestion(text=on_exhaustion.prompt), …)` via the same HIL write path Stage 6 introduced. Test asserts the HIL item lands and shows up in `/hil?status=awaiting`. |
| F2  | P2  | `.github/workflows/frontend.yml:45-54` | ESLint and Prettier steps are `continue-on-error: true` because flat config wasn't wired in Stage 11. With Stage 12 done, the wiring is in place — these soft gates should now be hard. Failing to remove them makes the acceptance criterion "frontend checks must be green" hollow. | Remove `continue-on-error: true` from both steps. Verify the lint and format steps pass, fix any drift. |

### Findings dropped after verification

Listed for the record so the next reviewer doesn't re-raise them:

- **Cache.bootstrap crash on corrupt JSON** — false positive. `dashboard/state/cache.py:163-166` already catches per-file parse/validation errors, logs at warning, and continues.
- **`paths.job_heartbeat()` missing** — false. It exists at `shared/paths.py:100` and the runner already uses it.
- **No torn-write test for `events.jsonl`** — partly false. `tests/job_driver/test_runner.py:700-731` covers it. Replaced by the narrower E5 above.
- **Pyright not in CI** — false. It's at `.github/workflows/backend.yml:64-65`. Path scope is narrower than ideal but the gate is real.

---

## Plan of attack

1. **Capture failing tests first.** For every P0 (A1, A2, A3, A4) and every P1 with a behavioural fix (A6, A7, A8, B1, F1) write a failing test before the fix. This is the same TDD pattern that landed Stages 5–8.
2. **PR 1 — backend correctness.** A1, A2, A3, A6, A7, A8, plus E1, E2. Single PR. `uv run ruff check . && uv run pyright shared/ dashboard/ && uv run pytest` green per commit.
3. **PR 2 — SSE end-to-end fix.** A4, A5, B1. Backend + frontend together because they're a single contract surface; reviewer needs to see both halves. Vitest + pytest + `vue-tsc` green per commit.
4. **PR 3 — doc + drift guard + cleanup.** C1, C2, C3, C4 (parallel-stages policy), D1, D2, E3, E4, F1, F2. Smaller, faster reviewable.
5. **Smoke script** `scripts/manual-smoke-stage12.5.py` exercises end-to-end:
   - Submit a fake job, complete a fake stage, assert cost rollup ≠ 0 (A3).
   - Open SSE on a real job, mutate a state file, assert frontend receives the live change (A4).
   - Hit `/sse/stage:bogus` and assert 422 (A1).
   - Trigger loop-back exhaustion, assert a HIL manual-step item appears (F1).
6. **Codex review** before opening each PR.

If A2 (validator enforcement) grows past ~300 LoC, split it out as a follow-up PR after the rest of 12.5 lands. Don't bundle a giant PR.

## Acceptance criteria

- [ ] Every P0 (A1, A2, A3, A4) has a failing test pre-fix and a passing test post-fix.
- [ ] Every P1 (A5–A8, B1, C3, C4, F1) has either a fix + test or an explicit `Decision: deferred because …` note added back into this doc.
- [ ] `uv run ruff check . && uv run pyright shared/ dashboard/ && uv run pytest` green on Python 3.12 and 3.13.
- [ ] `pnpm lint && pnpm vitest run && pnpm tsc --noEmit && pnpm build` green, **with `continue-on-error` removed from `frontend.yml`**.
- [ ] `docs/design.md` and `docs/implementation.md` no longer contain the C1/C2 ambiguities; A5 / A6 / C4 decisions are reflected in the design doc.
- [ ] `docs/stages/README.md` has a Stage 12.5 row pointing at this file.
- [ ] `scripts/manual-smoke-stage12.5.py` passes locally.
- [ ] No new feature surface introduced — diff is fixes, tests, doc edits, and CI gate-removal only.

## Notes for the implementing agent

- Audit ran on commit `8976555`. **Re-`Read` every cited file:line before committing any fix** — line numbers will drift the moment another commit lands on `main`.
- A3 (cost payload shape) is the single most-impactful fix. It's also the smallest — three files. Land it first; it unblocks meaningful cost-dashboard data for everything else.
- A4 + A5 together are the biggest single chunk: live SSE delivery is partly broken (named events) and partly unimplemented (events.jsonl tail). A5 is option (a) — wire the tail. Treat A4 + A5 as one PR (PR 2 in the plan of attack); they share contract surface and tests. If A5 alone runs past ~400 LoC, split it into its own PR and ship A4 first.
- For A2: if you discover the named validator registry needs more than a handful of entries, that's a sign the scope is larger than 12.5; carve out the new entries to a follow-up.
- For C3: the contract test should compare a *normalised* OpenAPI shape (sort keys, drop ordering noise) so trivial reorderings don't churn the test.
- C4 codifies a policy decision (no parallel stages in v0). The implementation is small (one validator + one design.md edit). The bigger thing is being explicit in `design.md` so future contributors don't re-litigate. Don't try to upgrade the field to a richer type or rename it; just document and reject.
