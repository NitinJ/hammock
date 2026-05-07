# Memory — design decisions and learnings

Things future agents (and humans) should know about why this code looks the way it does. Decisions, not how-tos. For how-tos, see `architecture.md`. For rules, see `rules.md`.

## Why agents run inside the project repo

Original v0 dispatch ran agents in a sandbox. Agents kept hallucinating symbols — `HIGHLIGHTER_COLORS` instead of the real `COLORS`, etc. — and the engine had no way to detect grounding failures.

Stage 3 of the workflow customization plan changed this: every agent node runs with `cwd = <job_dir>/repo` (or a worktree on the job branch). The agent now auto-loads project `CLAUDE.md`, can `Grep` and `Read` over real source. Design specs we've seen since this change quote actual code, cite real line numbers, and identify real risks (e.g., "`forEach` not index-based, so removal is safe").

The trade-off: agents get more context and run a few seconds longer. Worth it.

## Why every narrative type carries `document: str`

v0 design specs were free-text in a single string field. Reviewers couldn't render them; downstream agents had to parse prose to extract structure.

Stage 2 added a `document: str` markdown field on every narrative type (`bug-report`, `design-spec`, `impl-spec`, `impl-plan`, `summary`). The dashboard renders `document` as the primary view. Downstream agents read `document` directly — no JSON acrobatics.

Structured fields (`title`, `overview`, `proposed_changes`, etc.) are still there. The split is: **`document` for prose**, **other fields for what predicates / loop counts need**. `impl_plan.count` drives the implement-loop iteration count; that field has to stay structured.

## Why `schema_version: 1` is mandatory from day zero

Adding versioning to a yaml format *after* the format has shipped is much harder than adding it from the start. Stage 4 made `schema_version: 1` required on every workflow yaml — bundled, project-local, and test fixtures.

The friendly loader error (`engine/v1/loader.py`) names the file path and both versions and says "upgrade hammock or roll back the workflow". This is the load-time chokepoint that future schema evolutions will key off of.

## Why `_dispatch_human_node` writes state before the marker

Pre-Stage-3, the function wrote the pending marker first, then `_persist_state(BLOCKED_ON_HUMAN)`. On a slow CI runner (Python 3.13), the test thread polled, saw the marker, read `cfg.state` from disk between the two writes, and saw `RUNNING`. Test failed flakily.

Fix: swap the order. State first; once the marker is on disk, the state is already correct. See `gotchas.md` "HIL TOCTOU race".

This is the kind of race that's invisible on fast machines and unkillable in production. The lesson is broader than this one function: **if a public observer can detect either of two writes, do the writes in the order the observer expects them.**

## Why the engine spawns one driver subprocess per job

Alternative was: one driver process running all jobs concurrently. Rejected because:

- Driver crashes shouldn't take down all running jobs.
- Crash recovery is per-job (driver reads `cfg.state`, resumes from where it left off).
- Subprocess isolation makes the engine code simpler — no shared mutable state across jobs.

Cost: a few hundred MB of Python startup per concurrent job. We're not running thousands of concurrent jobs; this is fine.

## Why no DB

Hammock is a single-operator tool. State on disk under `~/.hammock/` is auditable, git-friendly (jobs can be tarred and shared), and cheap. Dashboard projects from disk on every request — no cache to invalidate, no schema migration when code changes, no "cold start" issues.

Limit: filesystem walks on big job dirs. Mitigated by: short retention (delete `~/.hammock/jobs/<old>` regularly), single-operator usage. If we ever need multi-operator, that's a v3 conversation.

## Why workflows are folders, not single yamls

Stage 1 moved bundled workflows from `<name>.yaml` to `<name>/workflow.yaml + <name>/prompts/<id>.md`. Reasons:

- Project-local customization wants per-node prompt files; flat layout couldn't support this.
- A folder is the natural unit for the "Copy to project" operation (Stage 6).
- Versioning is now per-folder; future schema-aware migration tooling has a clean target.

## Why prompts have three layers (header / middle / footer)

- **Header** is engine-controlled — node identity, retry context, working directory hint, branch info. Workflows don't customize this.
- **Middle** is per-node, customizable per workflow. Loaded from `prompts/<node_id>.md`. This is where teams encode their codebase's style, gotchas, conventions.
- **Footer** is type-driven — for each output, the type's `render_for_producer` describes the JSON shape, schema constraints, and where to write. This is the contract enforcement layer.

The split lets bundled workflows ship with sane defaults; project-local copies tune the middle without touching the contract; the engine guarantees the footer says the same thing every time.

## Why FakeEngine writes envelopes directly

Tests that use FakeEngine bypass the claude subprocess and write envelopes via the same code paths the real engine uses. This:

- Lets us test the state machine, projections, HIL flow without 30+ second real-claude runs.
- Doesn't catch prompt-tuning issues (real claude can do unexpected things).

The split: FakeEngine for orchestration logic, real claude for prompt + behaviour. `e2e_v1/` and dogfood runs cover the latter.

## Why `_resolve_project_local_workflow` precedes `_resolve_bundled_workflow`

A project that copies `fix-bug` into `.hammock/workflows/fix-bug/` (same name, no suffix) wants the project-local one used, not the bundled. Stage 5 made this the resolution order.

The default copy operation (Stage 6) suffixes the destination with `-<project_slug>` to avoid collision, so in practice this only matters when the operator explicitly renames their copy to match a bundled name. Still worth getting right for the rare case.

## Why we run `pytest` on both Python 3.12 and 3.13

CI matrix-tests both. Locally we ran only 3.12 for a long stretch. A real race in `_dispatch_human_node` only flaked on 3.13 because of slightly different scheduling characteristics (and slower CI runners).

Lesson: when CI runs on multiple versions, your local gauntlet must too. The `uv run --python 3.13 ...` invocation in `development-process.md` is mandatory.

## Why the bundled `write-design-spec.md` will get a two-phase rewrite (planned)

During first real-claude dogfood run, `write-design-spec` exited with empty result — agent did 3 research turns, decided it was done, never called Write. Tweaking the project-local prompt to "Phase 1 Research / Phase 2 Write" fixed it.

The bundled version still has the descriptive (not imperative) phrasing. Followup: rewrite all bundled `write-*.md` prompts to the two-phase pattern, OR (better) tighten the engine footer (`render_for_producer`) to be imperative everywhere. The latter is more durable — no per-prompt repetition.

This is in `project_hammock_dogfood_remove_lemon.md` user memory.

## Why the dashboard re-reads disk on every request

Same reason as "no DB": simplicity, auditability, no cache invalidation. Specifically:

- `GET /api/jobs/<slug>` reads `job.json`, walks `nodes/`, walks `variables/`.
- `GET /api/projects/<slug>/workflows` re-scans the bundled folder + the project's `.hammock/workflows/`.

Cost: tens of `os.listdir` per request. Acceptable at single-operator scale. Mitigated by: SSE invalidates frontend caches reactively; we don't poll.

If this becomes a bottleneck, the right fix is a small in-memory cache keyed on job-dir mtime, not a DB.

## What's intentionally NOT in v1

The scope was bounded for shippability. These are deliberate non-goals:

- **In-dashboard prompt editing.** Operators edit `.md` files in their IDE. The dashboard shows current contents read-only.
- **Project-local artifact types.** Adding a new typed field requires forking hammock. Per-project type extension is a v2 conversation.
- **Drift detection / sync** between a project's copy of a workflow and the current bundled version.
- **Variable substitution / templating** in middle prompts. Header inlines all input values; the middle is plain text.
- **Multi-tenant** — single operator, single hammock root.
- **Cloud agent execution.** Real claude is local subprocess only.

These are listed in `docs/hammock-workflow.md` "Out of scope for v1". Don't add them without the design conversation.

## Open followups (as of 2026-05-07)

These came out of dogfood and are worth tracking but didn't block any stage from merging:

1. **Engine footer should be imperative**, not descriptive. Currently `render_for_producer` says "Write your output as JSON to: ...". Should be "You **must** call the Write tool to write this JSON. Do not end the turn without writing it."
2. **Bundled prompt rewrite** — apply two-phase pattern to all `write-*.md`. Defence-in-depth above (1).
3. **`bin/preflight.sh`** — encode the full CI gauntlet as a script, with a CI test that diffs the script against `.github/workflows/*.yml`. The "remember to lint after format" rule belongs in a script, not in human memory.
4. **Splitting discovery from execution in subagent sweeps** — when delegating "find all sites and update them", the agent often misses one or two. Right pattern: agent does discovery, returns count, you compare against expectation, then agent sweeps.

## Code node narrative envelope (deferred from dogfood-fixes-2)

Today every artifact-narrative type carries `document: str` so reviewer/operator can read the agent's reasoning in the dashboard, and downstream agents inline the prose via `render_for_consumer`. **Code nodes have no equivalent.** Their reasoning lives in the commit message + PR body, both of which die outside Hammock — the dashboard's right pane on a code node shows only the `pr` envelope (branch, commit sha, url) with no narrative.

The dogfood-fixes-2 round added `document` to `review-verdict` (the missing reviewer narrative). The code-node case is bigger and was deliberately deferred:

- **Where the prose belongs.** Adding `document: str` to `PRValue` is the obvious move, but the agent already wrote a PR body — duplicating that into a Hammock envelope is double-work. Worth a design pass on whether the PR body itself is the source of truth (engine reads it back) or whether `document` is independent.
- **Empty-output edge case.** `tests-and-fix` with `tests_pr?` optional can succeed with no envelope at all (no failing tests, no PR opened). Today the dashboard shows "Node completed — no output produced." (the dogfood-fixes-2 fallback). When code nodes gain `document`, the empty-when-no-PR branch needs a story too — maybe `document` is required-but-can-be-empty, or `summary` becomes the standalone prose slot.
- **Prompt + producer + tests.** `implement.md` and `tests-and-fix.md` would need imperative phrasing for the new field; `engine/v1/code_dispatch.py:_build_code_prompt` would render the slot in the footer; `pr_review_verdict` consumer rendering would inline `pr.document` so reviewer agents see the implementer's narrative.

Don't sneak this into a small PR. It's a v1+ design conversation.
