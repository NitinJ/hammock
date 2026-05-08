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

## Migration

One PR, big-but-bounded:

1. New `iter_token` helpers in `shared/v1/paths.py`. Update every existing path helper to take `iter_path: tuple[int, ...] = ()` and route through the token.
2. Update `_NodeContext.expected_path` (artifact + code) to use the new layout.
3. Update `dispatch_loop` to thread `iter_path` through the body.
4. Update `dispatch_artifact_agent` and `dispatch_code_agent` to:
   - Receive `iter_path` parameter
   - Read agent's raw output from `runs/<n>/output.json`
   - Wrap and write envelope at the v2 path
5. Update `produce()` for every type — read raw output instead of envelope.
6. Update `resolver.py` and `predicate.py` to take `iter_path` context.
7. Update HIL pending marker writer + reader.
8. Update `dashboard/state/projections.py` — projections walk new layout. If we pick Option B for projections: resolver follows `$ref` files transparently; projections code reads through them.
9. Update frontend `schema.d.ts`, `JobOverview.vue`, `renderRows.ts` — universal iter_path.
10. **Live chat tail**: add `chat_appended` SSE event in `dashboard/api/sse.py` watcher; wire `AgentChatTail.vue` to refetch on event; preserve scroll position when not at bottom.
11. Wipe all existing on-disk jobs (pre-v1 dogfood data; no production users).
12. Migrate test fixtures (~30 files; mechanical).
13. Add a regression suite: T7 = "outer until-loop with `needs-revision` then `approved`, two outer iters, asserts no state leak."

Tests we can delete (covered by v2's invariants):
- `test_nested_hil_pending_carries_full_iter_path` (subsumed)
- `test_node_detail_excludes_outer_loop_projection` (loop projections are gone — see next section)

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

### My lean

**Option B** for these reasons:

1. Eliminates the 3-copies bug class structurally, not by filter heuristic.
2. YAML ergonomics unchanged — workflow authors keep writing `$design_spec`.
3. Disk-space and traceability both improve.
4. Migration cost is small (write the ref file at loop-finish time + add ~15 lines to resolver).

Option C is more pure-KISS but the cost (every workflow YAML changes, all consumer references become loop-aware) outweighs the simplicity win — especially for the project-local custom workflows operators have already written.

If we pick B, the resolver gains one rule: "if a variable file is `{\"$ref\": \"...\"}`, follow it." Everything else stays the same.

I want a yes/no on B before locking the migration plan. The path scheme depends on it.

## Open questions

1. **Iter token format**: `i0_1` vs `0-1` vs `0/1` (subdir). I lean `i0_1` (flat string, sortable, no path-traversal risk in URLs).
2. **Variable separator**: `<var>__<token>.json` (double-underscore) vs `<var>.<token>.json`. Either works; double-underscore avoids confusion with file extensions.
3. **`output.json` shape**: bare value JSON (today's de-facto) or already-wrapped-but-without-envelope-meta. I lean bare value — simpler agent prompt.
4. **Attempts numbering**: `runs/1/`, `runs/2/`, etc. per `(node_id, iter_path)` — or globally per node_id. Per-iter feels right (a fresh iter starts at attempt 1).
5. **Predicate footgun (already partly fixed)**: keep the load-time hard-reject for bare-ref `until` on non-bool? Yes, it stays.

## Sequencing

If you greenlight this:

1. Lock the open questions above (one round of discussion).
2. Branch `loop-execution-v2`. Single PR, mechanical migration. Probably 600–900 LOC delta. No new features — just the keying change + raw-output split + lazy projections + frontend iter unrolling.
3. Wipe all jobs on disk.
4. Real-claude dogfood the same scenario that broke today.
5. If green: merge. If a new failure surfaces, it's a *different* class (not iter-keying or stale-envelope) and we know the model held up.

After this lands, the patches I expect will go from "every dogfood reveals a structural bug" to "every dogfood reveals a prompt-tuning issue." Different problem class.
