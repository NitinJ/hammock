# Loop execution model

Status: design — not yet implemented. Supersedes the patchwork of per-symptom fixes that landed in dogfood-fixes-1, dogfood-fixes-2, and the iter_path retrofit.

## The fault we keep paying for

Each node execution is uniquely identified by **(node_id, iter_path)** where `iter_path` is a tuple of ints, one per enclosing loop, outermost first. A leaf node inside `outer-loop` (iter 0) → `inner-loop` (iter 1) has identity `(leaf, (0, 1))`.

Today, the persistence layer only stores **part** of that key:

| Layer | Today's key | Missing |
|---|---|---|
| `nodes/<id>/state.json` | `node_id` | iter_path entirely |
| `nodes/<id>/runs/<n>/` | `node_id, attempt_int` | iter_path entirely |
| `variables/loop_<inner>_<var>_<iter>.json` | innermost loop_id + that loop's iter | outer-loop iter context |
| HIL pending marker | `node_id` + (recently) `iter_path` | was patched, fragile |
| Frontend left pane | unrolls innermost iter only | outer iters invisible |
| Predicate evaluator | innermost loop's `[i]` | reads stale outer-scope envelopes |

Every dogfood failure of the last three weeks is one of those rows being silently lossy. Patching them one at a time produces correlated bugs every time we exercise a new scenario.

## The model, in three sentences

1. **Every execution is `(node_id, iter_path)`.** That tuple is the only key the persistence layer uses.
2. **Loop dispatch advances `iter_path`, then dispatches the body.** Body nodes don't carry "I am in a loop" state — they look up their iter_path from the dispatcher's call frame.
3. **The agent's raw output and the durable envelope are different files.** Stale reads stop being a failure mode.

That's it. Everything else is a consequence.

## Path layout

`iter_token` is a stringification of `iter_path`:

- `()` → `top`
- `(0,)` → `i0`
- `(0, 1)` → `i0_1`
- `(2, 0, 4)` → `i2_0_4`

ASCII-only, sortable, no separator collision with other path parts.

```
~/.hammock/jobs/<slug>/
├── job.json
├── workflow.yaml                    (snapshot at submit time)
├── driver.log
├── repo/                            (project clone)
├── repo-worktrees/<node_id>/<iter_token>/    (code nodes)
├── nodes/
│   └── <node_id>/
│       └── <iter_token>/            ← new axis
│           ├── state.json           ← (node_id, iter_path) state
│           └── runs/
│               └── <attempt>/
│                   ├── prompt.md
│                   ├── chat.jsonl
│                   ├── stderr.log
│                   └── output.json  ← agent's RAW output, distinct from envelope
├── variables/
│   └── <var>__<iter_token>.json     ← envelope, keyed by full iter_path
├── pending/
│   └── <node_id>__<iter_token>.json ← HIL markers
└── job-driver.pid
```

Two changes to highlight:

- **Iter token is a directory under `nodes/<id>/`, not a flat suffix.** `nodes/write-design-spec/i1/state.json`. Cheap to enumerate "all iters of this node".
- **Variable filename uses `__` separator.** `design_spec__i0.json`, `bug_report__top.json`. Even top-level vars get a token (`top`) so the layout is one rule, not two.

`shared/v1/paths.py` becomes the only file that knows the format. Two new helpers: `iter_token(iter_path: tuple[int, ...]) -> str` and `parse_iter_token(token: str) -> tuple[int, ...]`. Everything else calls these.

## Agent output ≠ envelope

Today: agent writes value-JSON to the **same path** the engine wraps into an envelope at. When the agent fails to write (claude empty-stdout), the engine reads the previous envelope, tries to validate it as a value, crashes — exactly the bug in `2026-05-08-...-47cfa8`.

v2 split:

- Agent writes to `nodes/<id>/<iter_token>/runs/<n>/output.json` (raw value-JSON).
- After agent exits, engine reads `output.json`, validates against the type's `Value` model, wraps in `Envelope`, and writes to `variables/<var>__<iter_token>.json`.
- If `output.json` doesn't exist: hard fail, no fallback. Eliminates the stale-read class entirely.

Cost: one more file per attempt (cheap). Benefit: agent failure modes can't masquerade as "wrong envelope shape" anymore.

## Loop dispatch (the only execution code that changes)

Today's `dispatch_loop`:

```
for iter in range(count):
  dispatch_body(body, loop_id=L, iteration=iter)
```

v2's `dispatch_loop`:

```
for iter in range(count):
  current_iter_path = enclosing_iter_path + (iter,)
  for body_node in body:
    if body_node.kind == "loop":
      dispatch_loop(body_node, iter_path=current_iter_path)
    else:
      dispatch_body_node(body_node, iter_path=current_iter_path)
```

Body nodes look up state by `(node_id, current_iter_path)` — no special-case "I'm in a loop" branch. **Outer iters advancing → inner state files don't exist for the new iter_path → fresh dispatch automatically.** No state leaks between outer iterations.

The substrate concept (per-iter vs shared worktree for code nodes) is **orthogonal** to iter_path keying. Keep it as-is. The reason `until` loops use a shared worktree is so the agent can revise across iterations — that's intentional. iter_path keying just makes sure the *envelope and state* don't collide; the worktree's "shared across iters" semantics is unaffected.

## Predicate + variable resolution

`$inner.var[i]` resolves with the current execution's iter_path as context. The resolver computes the full iter_token from `(enclosing_iter_path[:depth_of(inner)] + (i,))`. The resolver's signature gains one parameter (the caller's iter_path) and the rest is a path lookup.

`[last]`, `[i-1]`, `[*]` all collapse to "construct the right iter_token, then read". No special cases.

## UI implications

`JobDetail.nodes` is already a flat list with `iter_path`. v2 needs the projection code to:

1. **Show every (node_id, iter_path) that has a state.json.** Today, frontend only renders inner-iter unrolling. With v2, outer iterations naturally produce more rows.
2. **Header rows per loop iteration.** Left pane shows: `Design spec — review cycle (iter 1)` as a section header above the body rows for that iter.
3. **Click → URL `?node=<id>&iter=0,1`.** Iter-tuple-aware. Already partially in place (post-dogfood-fixes-2 nested HIL fix). Make it the universal pattern.
4. **Chat tail and node detail keyed by `(node_id, iter_path, attempt)`.** Endpoint becomes `GET /api/jobs/<slug>/nodes/<id>/iter/<token>/state` and `…/runs/<attempt>/chat`.

## Live SSE stream of the agent's chat (in scope)

Today: `chat.jsonl` accrues on disk while claude runs (stream-json output, line-buffered subprocess stdout → file). The frontend's `AgentChatTail` only fetches once on click and won't reflect new turns until the user navigates away and back.

v2 adds:

- **Backend.** The SSE pipeline already watches mtimes for envelope/state files. Add `chat_appended` events keyed by `(node_id, iter_token, attempt)`. Coalesce: emit at most one event per (key, 500ms window). Payload is bare — just the key. No content in the SSE payload.
- **Frontend.** `AgentChatTail.vue` subscribes to `chat_appended` for the currently-displayed `(node_id, iter_path, attempt)`. On event, refetch the chat endpoint and re-render. Auto-scroll-to-bottom only when the user is already at-or-near bottom (don't yank the scroll if they've scrolled up to read earlier turns).

KISS-on-purpose:

- No byte-offset / incremental fetch — refetch whole chat on every SSE poke. ~5 KB of JSON per turn × ~10 turns × 1 refetch per few seconds = trivial. Optimize later if it shows up in profiles.
- No partial-message streaming (claude's `--include-partial-messages`). Whole turns only. The user wants to *see what the agent's doing right now* — turn granularity is enough.
- Stale events are harmless: if the user already navigated away, the listener is gone.

## What v2 explicitly does NOT add

- **No retry-from-failure UI affordance.** Defer. The engine already supports `attempts` increment by re-running.
- **No diff between two iterations of the same node** (e.g., "show me what changed between design-spec iter 0 and iter 1"). Defer.
- **No multi-attempt-aware HIL inbox.** A single pending marker per (node_id, iter_path) is enough — humans don't need to see "approve attempt 2 of iter 1 of write-design-spec". They see "approve write-design-spec, iter 1".
- **No partial-message streaming inside a turn.** Turn granularity is enough; per-token streaming is a v2+ concern.

## What v2 deletes

- The "stale envelope re-read" failure mode (file split fixes it).
- The "outer iter doesn't reset inner state" bug class (full-iter keying fixes it).
- The "innermost-only iter on disk" lossiness (filename includes full token).
- The custom HIL iter_path retrofit logic — replaced by the universal scheme.

## Migration stages

The migration lands as a single PR `loops-v2` against `main`. Within the PR, work splits into 5 stages. **Each stage has a concrete smoke test that says "this stage is done." Do not start stage N+1 until stage N's smoke test passes.**

When dispatching an agent for a stage, point them at this section: "Read `docs/loop-execution-model.md` → "Migration stages" → implement Stage X end-to-end → run the stage's smoke test → commit and report."

### Stage A — Path scaffolding ✅ DONE (commit `954a9a9`)

Adds `iter_token` helper and updates path helpers to take optional `iter_path` parameter. Old helpers retained as deprecated shims so the codebase imports cleanly during stages B-E.

**Files**: `shared/v1/paths.py`, `tests/shared/v1/test_paths.py`

**Smoke test** (PASSING):
- `.venv/bin/pytest tests/shared/v1/test_paths.py -q` — 29 tests pass
- Round-trip check: `iter_token((0,1)) == "i0_1"` and `parse_iter_token("i0_1") == (0,1)`
- Top-level: `iter_token(()) == "top"`

### Stage B — Engine migration ✅ DONE (commits `55383e5`, `509c691`)

Migrate the engine end-to-end: dispatchers thread `iter_path`, agent raw output split, resolver `$ref` follow, loop projection writes. After this stage the engine is fully on the new keying. Deprecated path shims from Stage A get DELETED at the end of this stage.

**Files** (in dependency order — touch them in this order to keep the codebase compiling at each save point):
1. `engine/v1/loop_dispatch.py` — `dispatch_loop` threads `iter_path: tuple[int, ...]` through to body dispatchers.
2. `engine/v1/artifact.py` — `_NodeContext` accepts `iter_path`; `expected_path()` returns the v2 path; new `attempt_output_path()` returns `attempt_dir / "output.json"`.
3. `engine/v1/code_dispatch.py` — same pattern as artifact.
4. `engine/v1/driver.py` — top-level dispatch with `iter_path=()`; pending markers at `pending_marker_path(slug, node_id, iter_path)`.
5. `shared/v1/types/<each>.py` — `produce(decl, ctx)` reads from `ctx.attempt_output_path()` (raw value-JSON the agent wrote). Validates against `Value` model. Returns. Wrapping into envelope is done by the dispatcher, not by `produce`. `render_for_producer` instructs the agent to write to that path.
6. Dispatcher post-claude flow: read `output.json`. If missing or empty → hard fail. Else validate → wrap via `make_envelope` → write to `variable_envelope_path(slug, var, iter_path)`.
7. `engine/v1/resolver.py` + `engine/v1/predicate.py` — read variable through `variable_envelope_path(slug, var, iter_path)`. If file content is `{"$ref": "<stem>"}`, follow once to source path. Multi-hop chaining not allowed (raise on chain).
8. `engine/v1/loop_dispatch.py` (post-iter): for each loop `outputs:` declaration:
   - `[last]` / `[i-1]` / single-iter selectors → write `{"$ref": "<source-stem>"}` pointer file at outer-scope path
   - `[*]` aggregations → materialize the actual `list[T]` envelope (values are small lists of refs to body envelopes; aggregation is unambiguous; pointer-file approach doesn't fit because there's no single source)
9. `shared/v1/paths.py` — DELETE `loop_variable_envelope_path` and `_safe_loop_id` shims.
10. `hammock/templates/workflows/*/prompts/*.md` — verify no hardcoded path references that conflict with the agent's new write target. Update if needed.

**Smoke test**:
- `.venv/bin/pytest tests/engine/v1/ -q` — all engine unit tests pass
- `.venv/bin/pytest tests/integration/test_harness.py -q` — FakeEngine end-to-end harness passes
- `git grep -n "loop_variable_envelope_path\|_safe_loop_id" engine/ shared/` — zero hits (shims deleted)
- New on-disk shape verified: drive a 2-deep nested fake-engine workflow, then assert:
  - `nodes/<id>/i0_1/state.json` exists; `nodes/<id>/state.json` does not
  - `variables/<var>__i0_1.json` exists; `variables/loop_<id>_<var>_<iter>.json` does not
  - Outer projection at `variables/<var>__i0.json` is `{"$ref": "<stem>"}` (text match), not a copy of the source envelope
- Manual: read a `$ref` pointer file via `cat` — content is `{"$ref": "<stem>"}` JSON, exactly. Resolver test reads through it transparently.

**Done when**: ALL bullet points pass.

### Stage C — Dashboard backend ✅ DONE (commits `31dd184`, `fd4d406`, `f4d31a3`, `7da1d51`)

Update projections, chat endpoint, and SSE pipeline to use the new keying. Test fixtures in `tests/integration/dashboard/` migrate as part of this stage (since the projections move to new paths).

**Files**:
- `dashboard/state/projections.py` — walk `nodes/<id>/<iter_token>/state.json`; `node_detail` filters envelopes by exact `(var_name, iter_token)`; HIL queue reads from new pending marker path. DELETE `_envelope_belongs_to_node` heuristic — subsumed.
- `dashboard/state/chat.py` — `read_agent_chat(root, slug, node_id, iter_path, attempt)` reads from `nodes/<id>/<iter_token>/runs/<n>/chat.jsonl`.
- `dashboard/api/jobs.py` — chat endpoint route becomes `GET /api/jobs/{slug}/nodes/{node_id}/iter/{iter_token}/chat?attempt=<n>`.
- `dashboard/api/sse.py` — emit `chat_appended` event for `(slug, node_id, iter_token, attempt)`. Coalesce: at most one event per (key, 500ms window). Empty payload — frontend refetches.
- DELETE the iter_path retrofit logic from dogfood-fixes-2 era — subsumed by universal keying.
- Test fixture migration: `tests/integration/dashboard/` files that hand-construct old paths or seed envelopes via `loop_<id>_...` patterns.

**Smoke test**:
- `.venv/bin/pytest tests/integration/dashboard/ -q` — all dashboard tests pass
- Hand-craft a job dir with nested loop state on disk, then `GET /api/jobs/<slug>` returns one `NodeListEntry` per `(node_id, iter_path)` discovered
- `GET /api/jobs/<slug>/nodes/<id>/iter/<token>/chat?attempt=1` returns parsed turns from `nodes/<id>/<token>/runs/1/chat.jsonl`
- HIL queue: pending marker at `pending/<id>__<token>.json` shows up in `GET /api/hil/<slug>` with the correct `iter` array
- Manual SSE check: touch a `chat.jsonl` file, observe `chat_appended` event on `GET /sse/job/<slug>` within 500ms
- `tests/integration/dashboard/test_nested_hil_pending_carries_full_iter_path` and `test_node_detail_excludes_outer_loop_projection` either pass with the new keying OR get deleted as subsumed (call this out in the commit message)

**Done when**: ALL bullet points pass.

### Stage D — Frontend ✅ DONE (commits `77a1f64`, `e6c108f`, `1040207`, `914ddb8`)

Universal iter_path handling + live chat tail subscription.

**Files**:
- `dashboard/frontend/src/api/schema.d.ts` — `AgentChatResponse` and any iter-related types match Stage C
- `dashboard/frontend/src/api/queries.ts` — `useAgentChat(jobSlug, nodeId, iterPath, attempt)`
- `dashboard/frontend/src/views/JobOverview.vue` — `selectNode` emits iter_path; URL is `?node=<id>&iter=0,1`; chat tail receives iter_path
- `dashboard/frontend/src/components/jobs/AgentChatTail.vue` — subscribe to `chat_appended` for current `(node_id, iter_path, attempt)`; on event, refetch chat. Auto-scroll only if user is within ~50px of bottom; else preserve scroll
- `dashboard/frontend/src/composables/useSse.ts` — `chat_appended` in event union
- `dashboard/frontend/tests/e2e/_seed.ts` — `seedNode` accepts `iterPath`, `seedChat` writes to new path

**Smoke test**:
- `cd dashboard/frontend && pnpm test` — all vitest tests pass
- `pnpm type-check`, `pnpm format`, `pnpm build` — clean
- `pnpm test:e2e` — all Playwright tests pass, including new ones:
  - Nested loop fixture: left pane shows iter rows for both outer iters; clicking iter 1's body opens its chat tail
  - Live chat update: simulate appending a turn to chat.jsonl, observe new turn appearing in tail without manual refresh
  - Scroll preservation: scroll up, simulate poke, assert scroll position not changed

**Done when**: ALL bullet points pass + at least one new live-chat-update Playwright case passes.

### Stage E — T7 regression + final cleanup

Add the integration regression that prevents the bug class we set out to kill. Final docs updates. Full gauntlet end-to-end.

**Files**:
- `tests/integration/test_loops_v2_no_state_leak.py` — NEW. T7: outer until-loop with body containing write-design-spec + review-human; verdict alternates needs-revision (iter 0) → approved (iter 1). FakeEngine drives it. Asserts:
  - After outer iter 0: state files at iter token `i0` for body nodes
  - After outer iter 1 starts: fresh state files at `i1` — old `i0` state files preserved, distinct content
  - Variable envelopes: `design_spec__i0_0.json` and `design_spec__i1_0.json` are distinct files
  - Projection at `design_spec__top.json` is `{"$ref": "design_spec__i1_0"}` (the last iter)
  - Resolver returns iter-1 content when reading `$design_spec`
- `docs/loop-execution-model.md` — drop status markers as stages complete; finalize the `[*]` materialization choice in Stage B as a clarification
- `docs/for_agents/gotchas.md` — annotate "producer_node is for traceability" with "(structurally fixed in v2)"
- Any leftover test fixture sweep (most should be done in Stages B-D as their tests touch them)

**Smoke test (the FULL gauntlet)** — none can be skipped:
- `ruff format` (root) — clean
- `pyright shared/ dashboard/` — strict, 0 errors / 0 warnings
- `.venv/bin/pytest -q` — Python 3.12, all green
- `uv run --python 3.13 ... pytest -q` — Python 3.13, all green
- `cd dashboard/frontend && pnpm format && pnpm type-check && pnpm test && pnpm build && pnpm test:e2e` — all clean
- `ruff check` — clean (LAST, after formatters)
- `pnpm lint` — clean (LAST)
- T7 regression specifically green
- `git grep -n "loop_variable_envelope_path\|_safe_loop_id"` — zero hits anywhere

**Done when**: full gauntlet green + branch ready to push and PR.

## Tests we can delete (subsumed by v2's invariants)

These live tests target the patches we made along the way. Once the structural fix lands, they're either redundant or wrong:

- `test_nested_hil_pending_carries_full_iter_path` — pending marker keying is now universal, not retrofitted
- `test_node_detail_excludes_outer_loop_projection` — projection filtering is structural via `$ref` pointer files, not heuristic

These should be deleted in Stage C (dashboard projections) or Stage E (cleanup) — pick whichever is cleaner.

## Loop output projections — the unfinished decision

### What they are

A loop's `outputs:` block declares "values from inside the body that should be visible OUTSIDE the loop, by name." Example from bundled `fix-bug`:

```yaml
- id: design-spec-loop
  kind: loop
  until: $design-spec-loop.design_spec_review_human[i].verdict == 'approved'
  body:
    - id: write-design-spec
      outputs: { design_spec: $design_spec }
    - id: review-design-spec-human
      outputs: { verdict: $design_spec_review_human }
  outputs:
    design_spec: $design-spec-loop.design_spec[last]
    # ↑ make the last iteration's design-spec visible OUTSIDE this loop
    #   under the name `design_spec`, so impl-spec-loop downstream can
    #   reference $design_spec naturally.
```

`[last]` picks one iteration; `[*]` aggregates all into a `list[T]`.

Without projections, every downstream consumer would have to know the loop's structure: `$design-spec-loop.design_spec[3]` instead of `$design_spec`. Loops would leak into every consumer's reference syntax.

### What we do today (eager copy)

When `design-spec-loop` finishes:

1. Engine evaluates `$design-spec-loop.design_spec[last]` → reads the body's envelope at `loop_design-spec-loop_design_spec_<last>.json`.
2. Engine writes a **copy** of that envelope at the outer scope: `variables/design_spec.json` (or, if the loop itself is inside another loop, at `loop_<outer>_design_spec_<outer-iter>.json`).
3. The copy preserves `producer_node` so traceability stays intact.

For a 2-deep nest, the **same body envelope ends up duplicated three times on disk** (body's path + inner-loop's projection + outer-loop's projection). That's the "3-copies bug" we already patched once at the projection-filter layer, with `_envelope_belongs_to_node` heuristics.

### What v2 wants (full-iter-token keying)

With v2's path scheme, the body's envelope sits at one path: `variables/design_spec__<full-iter-token>.json`. Projections are about making that envelope **also reachable** at outer-scope paths so consumers can write `$design_spec` without knowing the loop structure.

Three options. They differ on what "also reachable" means.

### Option A — keep eager copy (today's pattern, just re-pathed)

After the loop, write a duplicate envelope to `variables/design_spec__<outer-iter-token>.json`. Resolver doesn't change.

- **Pros**: Resolver code is unchanged. Familiar.
- **Cons**: Same data duplicated on disk (design_spec.document can be 10 KB+). Filtering "is this node's direct output?" is back to today's heuristic — once we add another nesting level the 3-copies bug returns under a new name. Producer_node preservation across copies remains tricky.

### Option B — lazy projection via JSON ref file (recommended)

After the loop, write a tiny pointer file at the outer scope:

```json
// variables/design_spec__top.json
{"$ref": "design_spec__i0_2"}
```

Resolver, when reading `$design_spec`, opens the file. If it has `$ref` and no other fields, follow it to the actual envelope and return that. Otherwise return as-is.

- **Pros**: One envelope copy on disk regardless of nesting depth. "Direct output" filtering is structural — only the file at `<var>__<iter-token>.json` containing a real envelope is the source. Pointers are obviously pointers. Disk space O(1) per projection.
- **Cons**: Resolver gains ~15 lines. One indirection on read (negligible cost — the file is already memory-mapped by the OS).

### Option C — no projections, consumers walk loops

Drop `outputs:` on loops entirely. Consumers explicitly say `$design-spec-loop.design_spec[last]`.

- **Pros**: Simplest. No new concept. No projection logic anywhere.
- **Cons**: Every workflow author needs to track the enclosing-loop structure. Reusable workflow fragments break (you can't write a node that references `$design_spec` without knowing whether it's wrapped in a loop). Today's bundled YAMLs would all need rewriting.

### Resolution — Option B (locked)

**Option B** for these reasons:

1. Eliminates the 3-copies bug class structurally, not by filter heuristic.
2. YAML ergonomics unchanged — workflow authors keep writing `$design_spec`.
3. Disk-space and traceability both improve.
4. Migration cost is small (write the ref file at loop-finish time + add ~15 lines to resolver).

Option C is more pure-KISS but the cost (every workflow YAML changes, all consumer references become loop-aware) outweighs the simplicity win — especially for the project-local custom workflows operators have already written.

The resolver's one new rule: "if a variable file is `{\"$ref\": \"...\"}`, follow it once to the source." `[*]` aggregations don't fit the pointer-file approach (no single source) and are the one case that materializes the actual `list[T]` envelope at the outer-scope path.

## Decisions locked

These were open during the design conversation; recording the resolution as canon:

1. **Iter token format**: `i0_1`. Flat string, sortable, no path-traversal risk in URLs.
2. **Variable separator**: `<var>__<token>.json` (double-underscore). Avoids confusion with file extensions.
3. **`output.json` shape**: bare value JSON. Simpler agent prompt; one less envelope layer in the agent's mental model.
4. **Attempts numbering**: per `(node_id, iter_path)`. Each iter starts at attempt 1.
5. **Predicate hard-reject** (from dogfood-fixes-2): bare-ref `until` on non-bool stays rejected at workflow load.
6. **Loop output projections**: lazy via `{"$ref": "<source-stem>"}` pointer files for `[last]`/`[i-1]`/single-iter selectors. `[*]` aggregations materialize the actual list at write time (small data, unambiguous, no single source to point at).
7. **Live SSE chat tail**: in scope. Whole-turn granularity. No partial-message streaming. Refetch-on-poke (no byte-offset diffing).
