# Gotchas

Concrete footguns observed in this codebase. Read this before opening a PR.

## CI gauntlet — order matters

`ruff check` and `pnpm lint` go **last**, after every formatter pass. Reason:

- `ruff format` and `pnpm format:fix` rearrange code. Reordering can introduce new lint findings — most commonly import-order (`I001`).
- If you run lint, then format, you'll push code that fails lint in CI even though local lint passed.

Always chain: `format → lint`. Or use a single preflight script. See `feedback_full_ci_gauntlet.md` in user memory for the long version.

## CI gates that are easy to skip

Three specific gates are not in the obvious "ruff + pytest + vitest" sequence:

- `pyright shared/ dashboard/` — strict mode. Catches `list` (untyped) → must be `list[T]`. Catches private import (`_foo` outside its module). ruff doesn't catch any of this.
- `pnpm build` (vite) — type-check passing doesn't mean build passes. Bundle resolution can still fail on missing imports.
- `pnpm test:e2e` (playwright) — vitest is component-level; e2e tests run the full SPA against a live dashboard. The HIL form fixture often needs updating when narrative type contracts change.

`development-process.md` has the full preflight list.

## sed vs Edit on Python files

`sed -E 's/def fake(prompt: str, attempt_dir: Path):/def fake(prompt: str, attempt_dir: Path, cwd: Path | None = None):/'`

This breaks. `Path | None` contains `|` which sed treats as alternation in extended regex. Either:

- Use `Edit` / `Write` (always works, slower for big sweeps but never wrong).
- Or: escape with `\|` in the replacement and use basic regex — but you'll forget once and waste 20 minutes.

Rule: never `sed` files containing Python type annotations. The 30-second "speed" win is a lie.

## Real-claude prompts need imperative phrasing

If your prompt's outputs section just describes the contract ("Write your output as JSON to: ..."), real claude can:

1. Do the research turns (Grep, Read).
2. Decide it has enough context to "answer in chat".
3. Exit with `stop_reason: end_turn`, `result: ""` — no Write call, no output file.

Mock runners don't reproduce this. The only fix is imperative phrasing in the prompt:

```markdown
**Phase 1 — Research.** ...

**Phase 2 — Produce the output.** Use the Write tool to write the output JSON
to the path named in the `## Outputs` section. Do **not** end the turn until
you have called Write. The job fails if the output file is missing.
```

Diagnosing this in the wild: re-run with `claude --output-format json -p < prompt.md`. If `result: ""` and `num_turns: 4+`, that's the failure mode.

The bundled `write-design-spec.md` had this issue during dogfood. Followup: tighten the engine footer (`render_for_producer` on each type) to be imperative everywhere, so per-workflow prompts don't have to repeat it.

## Empty stdout from claude isn't always a clear failure

`claude -p` with `bypassPermissions` can return rc=0 with completely empty stdout. The dispatcher sees rc=0, runs `produce()`, finds the expected output file missing, and reports "output contract failed".

When debugging:

1. Don't trust the dispatcher error alone. Read `<job_dir>/nodes/<id>/runs/<n>/stdout.log` — it should have content.
2. If empty: re-run the saved `prompt.md` with `--output-format json` to see `num_turns`, `stop_reason`, `result`.
3. Common diagnoses:
   - Empty `result` + `stop_reason: end_turn` → prompt-tuning issue (see above).
   - `permission_denials: [...]` → bypassPermissions isn't doing what you think.
   - `result: "..."` but no file written → agent gave a chat answer instead of using Write.

## HIL TOCTOU race (fixed, but easy to reintroduce)

`engine/v1/driver.py:_dispatch_human_node` must:

```python
_persist_state(cfg, JobState.BLOCKED_ON_HUMAN, root=root)
write_pending_marker(...)
```

**State first, marker second.** Reverse this and any observer that detects the marker between the two writes reads `RUNNING` from `cfg.state` on disk. The test `test_driver_transitions_through_blocked_on_human` flaked exactly this way on Python 3.13 in CI before the fix.

If you're refactoring this function, read `rules.md` "State persistence ordering for HIL" first.

## Loop-indexed envelope path

When a loop body produces a variable, the envelope path is **not** `<job_dir>/variables/<var>.json`. It's `<job_dir>/variables/loop_<loop_id>_<var_name>_<iter>.json`. Use `paths.loop_variable_envelope_path(...)`, never construct it by hand.

Variable references inside loops use the `[i]` / `[last]` / `[*]` syntax to read these. Predicate evaluation handles the path resolution. If you're adding a test that seeds loop-indexed envelopes manually, use the helper.

## File-on-disk paths are the contract

Tests assert against the exact path layout. The dashboard's projections read this layout directly. The engine writes this layout. **Don't introduce alternate paths.** If you need a new file under `<job_dir>/`, add a helper to `shared/v1/paths.py`.

## Dashboard reads disk on every request — no caching

There's no DB. `dashboard/state/projections.py` re-reads `~/.hammock/jobs/<slug>/job.json`, walks node dirs, etc. on every request. This means:

- Your test that writes to disk is observable to the next API call without any "wait for cache invalidation".
- A slow filesystem (CI) can make endpoints slow. Don't add filesystem walks in hot paths.
- Atomicity matters: writing partial state will be observed. Use `shared.atomic.atomic_write_text` for any file the dashboard reads.

## SSE polling is not free

The SSE pipeline (`dashboard/api/sse.py`) polls file mtimes. Don't add expensive operations to its watch loop. Don't add new SSE event types without thinking about the polling cost — coalesce where possible.

## `pnpm build` is part of the gauntlet

Vite build can fail on imports that pass `vue-tsc --noEmit` (type-check). Specifically: importing a default export that doesn't exist, or a circular dependency. Run `pnpm build` before pushing.

## Real claude needs cwd inside the project repo

Stage 3 made every agent node's cwd `<job_dir>/repo` (or a worktree on the job branch). If you're refactoring the dispatcher and considering "let's just default cwd to None for tests", DON'T. Pass an explicit cwd. The runner's cwd is part of the contract — it's how `CLAUDE.md` and project files become visible.

## `pre-merged-loop` envelope name is "pr_review", not "pr_review_human"

When approving a `pr-review-hil` HIL gate, the var_name in the answer payload is `pr_review` (matches the workflow's `outputs:` declaration), not the descriptive name. Likewise the value shape is just `{"verdict": "merged" | "needs-revision"}` — `summary` is engine-populated, not human-supplied. The 400 you'll get if you send extra fields is correct.

## Project memory loses recent context after compaction

If a long session compacts, the agent's working memory of recent decisions disappears. Make decisions stick by:

1. Updating user memory (`MEMORY.md` + per-decision file).
2. Updating in-repo docs when the decision is repo-scoped (`docs/for_agents/`).
3. Encoding the discipline as a script if it's a checklist (`bin/preflight.sh`).

Adding a third "remember to do X" memory note is the signal to convert X into a script.

## `producer_node` is for traceability, not for filtering

When a loop completes, its output projection (`outputs: x: $loop.x[last]`) re-writes the body's envelope at the outer loop's scope. The re-write preserves `producer_node` so the provenance chain stays intact — a downstream consumer that asks "who produced this design spec?" still gets `write-design-spec`, not `<loop:design-spec-loop>`.

This means: **you cannot identify a node's direct outputs by `producer_node == node_id` alone.** A 2-deep nested loop body's envelope appears at 3 paths (the body's own loop-indexed path + 2 outer projections), all with the same `producer_node`. Naive filtering surfaces three copies of the same envelope under the body node's detail page — exactly the bug we shipped and then fixed.

The right rule, used in `dashboard/state/projections.py:node_detail`:

- Top-level node → its direct path is `<var>.json`.
- Loop body node → its direct path is `loop_<innermost_parent_loop_id>_<var>_<iter>.json`.

Filter envelopes by path shape, then producer_node, in that order. Anything else is by-product of loop projection and belongs to the loop, not the body node.

If you're touching anything that walks `<job_dir>/variables/`, ask yourself: am I looking at provenance, or am I looking for the direct output? They're different queries.

## Always construct envelopes via `make_envelope`

A test fixture had `'"version":"1"'` in a hand-crafted JSON envelope. `Envelope` schema field is `type_version`, not `version`. The fixture's envelope was silently rejected by `Envelope.model_validate_json(...)` (extra-forbidden), the surrounding `try/except` swallowed the failure, and the test got back zero envelopes when it expected one. ~20 minutes of debugging.

Rule: any test that needs an envelope on disk uses `make_envelope(type_name=..., producer_node=..., value_payload=...)` and writes `envelope.model_dump_json()`. Never hand-craft JSON for envelopes. The schema can drift; `make_envelope` won't.

This applies to **every** envelope, including ones you're writing to simulate engine behaviour like loop output projections.
