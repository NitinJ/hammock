# Hammock design patch

A living design document capturing structural changes to Hammock arrived at through the post-dogfood brainstorm. Source pressures and bug catalogue live in `docs/notes/2026-05-04-hammock-design-findings.md`. Codex adversarial review of an earlier draft lives in `docs/notes/2026-05-05-codex-review-design-patch.md`; resolutions are integrated below. This doc is the resolution side: what we are committing to changing, in shape, before any implementation work.

## Guiding principle: YAML is agent-generated

Workflows are produced by agents, not hand-written by humans. The optimisation function for every design decision below is **engine simplicity over author ergonomics**. Verbosity is fine if it removes engine ambiguity. Multiple parallel declarations are fine if they help the validator. Explicit qualifiers everywhere beat shorthand inference.

This justifies decisions that would feel pedantic in a hand-authored YAML:

- Mandating `$loop-id.var[i]` everywhere instead of context-aware shorthand (§1.5).
- `?` markers on every optional input and output (§1.6).
- Substrate explicitly declared on every loop (§2).
- `Decl` and `Value` schemas for every typed variable (§1.4).

And rules out:

- Context-aware resolution that "guesses" what the author meant.
- Implicit defaults that depend on enclosing context.
- Shorthand variants of any declaration.

If a future need shifts the audience to humans, the syntax can sugar over the explicit form. The semantic model stays the same.

---

## 1. Workflow / stage / variable model

### 1.1 What a stage is

A stage is a node in a static DAG. A node carries:

- **Inputs** and **outputs** (typed, named).
- **before / after** relationships (DAG edges; not positional ordering in a list).
- **Actor**: who performs the work — `agent` | `human` | `engine`.
- **Kind**: a tag like `write` | `review` | `code-edit` | `expander` | `gate`. The kind drives what substrate the engine grants (worktree, branch, etc.) and how outputs are rendered in prompts. Kind is engine-facing; it routes machinery.
- **Contract**: the declared inputs must be present and typed before the node runs; the declared outputs must be produced and typed after the actor exits. The engine enforces both.

The substrate (worktree, branch, repo handle) is engine-provided based on the node's kind, not declared by the node. A `review` node never sees a worktree; a `code-edit` node gets one. The node never sees on-disk paths — it sees variables.

### 1.2 Workflow shape

The workflow is a static DAG, declared in YAML:

- Nodes are listed at the top level with their `kind`, `actor`, `inputs`, `outputs`, and `after:` (DAG edges).
- Loops are a node primitive: `loop: { count: $some_variable }`. Iterations are homogeneous in *contract* (same kind, agent, output shape, budget). The loop index `<i>` can parameterise prompt content but not contract.
- A loop body can itself be a sub-DAG (a sequence of nodes, all sharing the same `<i>`).
- **Workflow constants**: defined at the workflow YAML level, immutable, readable by all nodes (Make-variable style).
- **No mutable shared state**: the only writes are to declared output variables, owned by a single producer node.

### 1.3 Variables (first-class)

Variables are workflow-level, single-producer, typed. They replace path-based input/output today.

- **Closed type set for v1**: `pr`, `branch`, `review-verdict`, `pr-merge-confirmation`, `bug-report`, `design-spec`, `impl-spec`, `impl-plan`, `summary`, `job-request`. Generic `list[T]` and `Maybe[T]` derived automatically. Adding a new type later is bounded work — one class implementing the type protocol (§1.4), ~30-50 lines for simple types.
- **Per-variable config**: each variable carries a `VariableDecl` — a typed config object discriminated by type (its `Decl` Pydantic model). Same type, different decls means different per-variable behaviour. (Two `pr` variables can target different bases or have different draft flags via decl, not via type.)
- **Loop-produced variables are addressable** (see §1.5 for full rules): a body node's output is referenceable as `$loop-id.var[i]` inside the loop, `$loop-id.var[last]` outside, etc. The body's per-iteration write is still single-producer; the engine projects views.
- **On-disk layout** is engine-owned, flat, and operator-friendly. Indicative: `<job_dir>/loop_Impl_PR_5`.

### 1.4 The variable type protocol

```python
class VariableType(Protocol):
    name: ClassVar[str]                  # e.g. "pr", "review-verdict"
    Decl: ClassVar[type[BaseModel]]      # per-variable config schema (Pydantic)
    Value: ClassVar[type[BaseModel]]     # the produced value's schema (Pydantic)

    def produce(self, decl: Decl, ctx: NodeContext) -> Value:
        """Engine-side: after the actor finishes the node, produce the
        typed value. Raises VariableTypeError if the contract isn't
        satisfied — engine treats that as 'node failed contract'."""

    def render_for_producer(self, decl: Decl, ctx: PromptContext) -> str:
        """Prompt fragment for the producing node: what the actor must
        do to satisfy this output."""

    def render_for_consumer(self, decl: Decl, value: Value, ctx: PromptContext) -> str:
        """Prompt fragment for a downstream consuming node: how this
        upstream value appears."""

    def form_schema(self, decl: Decl) -> FormSchema | None:
        """For human-actor nodes producing this type. Returns None if
        the type is not human-producible. Default implementation derives
        the form from `Value`'s Pydantic schema; types override for
        custom widgets."""
```

Single-phase `produce`. No two-phase commit. (See §1.9 for the v1 limitation that follows.)

What the engine handles generically — types do not need to implement these:

- **Serialise / deserialise** — derived from `Value` (Pydantic gives this for free).
- **Field access** for predicates (`$impl_plan.count`, `$verdict.verdict`) — `Value` model introspection.
- **Truthiness** for `runs_if` — `present = truthy, absent = falsy`. One rule for all types.
- **List wrapping** (`list[T]`) — generic `ListType[T]` produced automatically by count loops.
- **Optional wrapping** (`Maybe[T]`) — generic, marker-driven via `?` syntax.
- **`Decl` validation** — the `Decl` Pydantic class itself is the validator.

Adding a new type = define `Decl`, define `Value`, implement 3-4 methods. That's the "one class per type" promise.

`NodeContext` (engine-owned, type uses) provides:

- `expected_path()` — where engine stores files for this variable.
- `actor_workdir` — worktree (if the node's kind grants one); else absent.
- `stage_branch`, `base_branch`, `repo` — set when the node has code substrate.
- helpers for engine-side mechanics: `git_push`, `gh_create_pr`, `branch_has_commits`, `gh_get_pr_state`.

The node itself never touches `NodeContext`; only `VariableType.produce` does.

### 1.5 Loop variable resolution

Variables produced inside a loop body are referenced with explicit, strongly-typed indexing. No shorthand, no context-aware inference. The author writes exactly what they mean; the validator and engine resolve mechanically.

#### 1.5.1 Reference forms

| Form | Meaning | Where legal |
|---|---|---|
| `$loop-id.var[i]` | Current iteration's value | Inside the named loop's body; producer must be upstream in this iteration's body DAG |
| `$loop-id.var[i-1]` | Previous iteration's value | Inside the named loop's body; consumer must mark the input optional (`?`) |
| `$loop-id.var[last]` | Final iteration's value | Outside the named loop |
| `$loop-id.var[*]` | List across all iterations | Outside the named loop |
| `$loop-id.var[k]` | Specific iteration index `k` (literal int) | Anywhere; runtime check `0 ≤ k < count` |
| `$loop-id.index` | Current iteration index of `loop-id` | Anywhere inside the loop's body or any nested body |

#### 1.5.2 Resolution rule for `[i]`

A reference `$loop-id.var[i]` inside an iteration of `loop-id` resolves to:

- The current iteration's value, *if* the producer node has executed in this iteration before the consumer (DAG-order check).
- The previous iteration's value, *if* the producer is "self" (a node feeding back from its own prior iteration) or comes later in the body DAG. In this case the consumer must mark the input optional, because iteration 0 has no prior.

The validator enforces this statically. A required (`non-?`) input referencing `[i]` must point at a producer upstream in the same-iteration body DAG.

#### 1.5.3 Outside-the-loop projection

Outside the loop body, only `[last]`, `[*]`, and `[k]` are legal. The engine derives the projected types:

- For a body variable of type `T`: outside, `$loop-id.var[last]` has type `T` (or `Maybe[T]` if the loop never iterated, e.g. `count: 0`).
- `$loop-id.var[*]` has type `list[T]`, possibly empty.
- `$loop-id.var[k]` has type `T` (runtime bounds-check).

#### 1.5.4 Nested loops

Each loop has its own ID and its own `i`. References are fully qualified with the loop ID. From inside an inner loop:

- `$inner-loop.var[i]` is the inner loop's current iteration.
- `$outer-loop.var[i]` is the outer loop's current iteration (whatever outer iteration is currently executing — the inner loop's existence doesn't change which outer iteration we are in).
- `$outer-loop.index` and `$inner-loop.index` are independent.

From outside the outer loop, references chain transparently: `$outer-loop.var[last]` resolves through whatever projection the outer loop applies to its body, which itself may contain inner-loop projection. No "parent" magic, no relative depth math.

There is no nesting depth cap.

### 1.6 Optional / Maybe semantics

Optional inputs and outputs are marked with a `?` suffix on the declaration. The engine treats `Maybe[T]` as a first-class concept derived from this marker.

#### 1.6.1 The three rules

1. **Validator: a required (`non-?`) input that references a `Maybe` variable must be guarded by a `runs_if` predicate referencing the same variable.** The predicate's truthiness check (`runs_if: $tests_pr`) is the presence narrowing. Without the guard, the validator rejects: "you read an optional variable as required without a presence guard."

2. **`after:` treats `SKIPPED` and `SUCCEEDED` identically.** A skipped node satisfies an `after:` constraint. So a downstream node waits only for upstream nodes to reach a terminal state — `SUCCEEDED | SKIPPED | FAILED | CANCELLED` — and then evaluates its own `runs_if`. No special handling.

3. **Skipped nodes produce no outputs.** A node skipped by `runs_if = false` writes no typed values. Consumers of those outputs see them as absent (matching the optional-input behaviour). No sentinel values, no nulls-on-disk; the variable simply isn't there.

These three rules together cover all the optional-output/Maybe interactions the design needs. No flow-typing inference, no narrowing scopes — just clear rules a validator and a runtime can both implement.

### 1.7 Engine responsibilities

The engine is the single contract enforcer. It owns:

- **Input pre-conditions**: every declared input variable has been produced by an upstream node before the node is dispatched.
- **Output post-conditions**: after the actor exits, the engine calls `produce` once per declared output. All must succeed.
- **Substrate provisioning**: worktree, branch, push to remote, PR creation. The mechanical work that today lives in agent prompt protocol moves into engine machinery driven by node kind and variable type.
- **Variable resolution** per §1.5: `$var`, `$loop-id.var[i|i-1|last|*|k]`, `$loop-id.index`. Engine translates references to concrete values at dispatch time.
- **Persistence with envelope**: every persisted variable is wrapped in an engine-owned envelope carrying `{type, version, repo, producer_node, produced_at, value}`. Resume after crash, or re-mounting the job dir in a different repo context, is safe because the envelope is self-describing. Type implementations don't see the envelope.
- **Crash recovery**: on driver (re)start, scan disk for the job's variable state and node state; resume from there. Required for any crash, not specifically HIL — but the long-lived driver model (§3) leans on it.
- **Workflow validator** (load time, before any execution): see §4 for the full check list.

### 1.8 What this collapses (vs. today)

- Naming-convention heuristics (`review-{X}-`, `write-X` prefix-strip) → explicit `after:` edges and `reviews:` declarations.
- `_SCHEMA_HINTS` dict in `prompt_builder.py` → per-type `render_for_producer`.
- 5-step PR protocol injected by string match → `pr.produce` does push + PR-create itself; agent only edits + commits.
- `outcomes.assert_summary_md_has_url` (string match on markdown) → structural check "did `pr` variable get produced?"
- `outcomes.assert_branches_exist` → folded into `pr.produce` and `branch.produce` (push fails ⇒ contract fails).
- `runs_if`-skipped stages forcing test special-cases → variable was either produced or it wasn't; engine knows authoritatively.
- HIL stitching reaching into `cache._scan` → engine reads variable state from disk authoritatively; cache is a derived view.
- Cleanup `git push --delete` from wrong cwd → cleanup operates on typed `branch` and `pr` records carrying their own repo identity.
- Dynamic stage-list mutation via `plan.yaml` merge → static DAG with loop primitive driven by upstream variables.

### 1.9 Deferred to v1+ (named, not avoided)

- **Two-phase produce / external-state rollback.** v1 single-phase `produce` means a side-effecting type (`pr`, `branch`) that succeeds before a later output fails will leave external state behind (orphan PR / pushed branch). Documented v1 limitation; operator cleans up. Revisit if it bites in practice.
- **Cross-output invariants enforced by node-kind orchestration** ("open PR only if tests passed"). Today this is the workflow author's responsibility — sequence stages so the invariant is upheld structurally. Revisit when we have a concrete case where structural sequencing isn't enough.
- **Open variable type set + plugin mechanism.** Adding a type is bounded work today (one class), but registering it is via the closed REGISTRY. Open this when an external integration requires it.

---

## 2. Node kind taxonomy

A node's `kind` exists to specify what substrate the engine grants the node before the actor runs. Anything else (prompt framing, evaluative tone, what kind of work the actor does) is a per-template concern.

### 2.1 The three kinds

**`artifact`**
- Engine grants: the job dir (typed variable read/write only).
- No worktree, no branch, no repo handle.
- Used for nodes that produce or consume typed artifacts without touching source code.

**`code`**
- Engine grants: the job dir, plus a per-stage git worktree and a stage branch forked off the job branch (see §2.4 for branch hierarchy and allocation rules).
- The worktree is the actor's working directory.
- Used for any node that reads, edits, or runs source code.

**`loop`**
- Engine grants: a scoped sub-DAG and an iteration index. No actor.
- Loop nodes do not "run" in the actor sense — they frame iteration over a body. The body's nodes (which can be any of the three kinds) are what actually execute.
- Carries `count` or `until`, `max_iterations` (required for `until`), `substrate` mode, body, and `outputs:` projection. See §5 for full semantics.

Actor (`agent` | `human` | `engine`) is orthogonal for `artifact` and `code`; not applicable for `loop`. Combinations seen in practice:

| kind     | agent              | human                     | engine                   |
|----------|--------------------|---------------------------|--------------------------|
| artifact | write, review      | HIL gate, PR-merge HIL    | pure data transform      |
| code     | edit code, fix bug | (rare; human in worktree) | run tests, run linter    |
| loop     | —                  | —                         | — (frames a sub-DAG)     |

### 2.2 Substrate contracts

For `kind: artifact`, `NodeContext` provides:

- `ctx.job_dir`
- `ctx.expected_path(var_name)` — resolves to a path under the job dir, owned by the engine.
- `actor_workdir`, `stage_branch`, `repo`, `base_branch` are not provided.

For `kind: code`, `NodeContext` additionally provides:

- `ctx.actor_workdir` — the per-stage worktree, created by the engine before the actor runs.
- `ctx.stage_branch` — the branch forked off the job branch, created/recovered by the engine per §2.4.
- `ctx.repo`, `ctx.base_branch` — repo identity and base.
- engine helpers: `git_push`, `gh_create_pr`, `branch_has_commits`, `gh_get_pr_state`.

For `kind: loop`, no `NodeContext` — the loop doesn't run; it dispatches its body.

The actor never sees on-disk paths for outputs. Outputs are written as typed variables; the engine resolves paths. For `code` kind, the worktree path is the only one the actor sees, and only because they edit code there.

### 2.3 What collapsed away from the earlier sketch

The first cut had five kinds: `write`, `review`, `expander`, `code-edit`, `gate`. Each collapses cleanly:

- **`review`** → `artifact` + agent. The "be critical" framing is a per-template prompt concern, not engine-level. The output type (`review-verdict`) is what's review-specific, not the kind.
- **`expander`** → `artifact` + agent producing a loop-driving typed variable. Dynamic-YAML behaviour disappears entirely; replaced by the static DAG plus loop primitive.
- **`code-edit`** → `code` + agent.
- **`gate`** → `artifact` + human. Human-gate specialness lives in the actor (engine renders UI for any human-actor node), not in the substrate.

Two engine-side substrate provisioners to maintain instead of five. Per-template differences (review tone, expander vs. write-summary) live in the prompt template and the output type, where they belong.

### 2.4 Branch hierarchy and substrate allocation

The engine owns three nested branch namespaces:

```
main                              (read-only base; engine never writes)
  └── hammock/jobs/<slug>         (job branch; created at job start)
        └── hammock/stages/<slug>/<node-id>     (stage branch per code-kind allocation)
              └── ... (further per-iteration variants if a substrate-shared loop reuses one)
```

Key properties:

- **Main is read-only** for Hammock during a job. It is the source of `hammock/jobs/<slug>` at creation; it is never written to until the human (out of band) merges the job branch.
- **Stage PRs target the job branch**, not main. When a `pr-merge-hil` is satisfied, the merge happens server-side on GitHub against the job branch.
- **The job branch advances** as stage PRs are merged into it. Subsequent stage allocations off the job branch see the merged work.

Substrate allocation rules per `kind` and `loop` configuration:

- **Top-level `code` node**: engine creates `hammock/stages/<slug>/<node-id>` off the job branch. Engine pulls the job branch first to pick up server-side merges from prior stages.
- **`code` node inside a `loop` body with `substrate: per-iteration`** (default for count loops): each iteration creates a fresh stage branch off the (just-pulled) job branch. Branch names are scoped to include the iteration index.
- **`code` node inside a `loop` body with `substrate: shared`** (default for until loops): a single stage branch is created at loop start and reused across iterations. The branch persists across retries; the actor's previous-iteration commits are visible.
- **`code` node inside a code-substrate sub-context**: inherits the parent's substrate (no new branch). Used implicitly when an inner loop does not declare its own substrate.

Corner case: **branch missing at allocation time.** If the engine expects to find a branch (e.g. mid-run) but it has been deleted (user housekeeping after a merge), the substrate allocator re-forks from the job branch. No-op recovery — the assumption is the prior work was already merged into the job branch, so re-forking gets us a substrate equivalent to what was there.

Engine never modifies main. The job branch's eventual merge to main is the human's call, outside Hammock.

v1 limit: if main is force-pushed concurrently by an unrelated human, or the job branch is rewritten externally, the engine forks off "whatever is there now." For the user-and-cofounder scale this is not a real concern; flagged but not handled.

### 2.5 What this nails down permanently

The JOB_DIR vs cwd confusion stops being a thing:

- An `artifact` kind has only a job dir. There is no cwd ambiguity because there is no cwd to confuse.
- A `code` kind has both. The engine renders the prompt with both clearly labelled and tells the actor once: "code edits go in `actor_workdir`; structured outputs are produced as typed variables (engine resolves their paths)." The five-line "remember, JOB_DIR is X and cwd is Y" boilerplate that we repeated in every stage prompt during the dogfood collapses to a per-kind default the engine renders once.

### 2.6 Mapping today's stages to the new model

| Stage role                     | Kind     | Actor  | Notes |
|--------------------------------|----------|--------|-------|
| write-bug-report               | artifact | agent  |  |
| write-design-spec              | artifact | agent  |  |
| review-design-spec-agent       | artifact | agent  | output type = review-verdict |
| review-design-spec-human       | artifact | human  | engine renders UI, captures verdict |
| write-impl-spec / impl-plan    | artifact | agent  | impl-plan output exposes `.count` |
| implement (per loop iteration) | code     | agent  | `pr` type does push + open PR |
| pr-merge-hil                   | artifact | human  | human merges on GitHub, engine verifies |
| tests-and-fix                  | code     | agent  | optional `pr` output if fix needed |
| tests-pr-merge-hil             | artifact | human  | conditional; runs only if tests-and-fix produced a PR |
| write-summary                  | artifact | agent  |  |
| design-spec-loop / agent-loop  | loop     | —      | until loops (review approval) |
| implement-loop                 | loop     | —      | count loop driven by `$impl_plan.count` |
| pr-merged-loop (inside above)  | loop     | —      | until loop (PR merged) |

---

## 3. Human-in-the-loop (HIL)

### 3.1 Problem

Today's HIL has two structural issues:

1. **Cache acts as gatekeeper.** Disk and the in-memory cache disagree; tests had to reach into private cache methods to make submitted items visible. The cache is treated as authoritative when it should be a derived view.
2. **Wake-up is the caller's responsibility.** When the driver hits a human gate it exits, and whoever submits the answer must remember to respawn the driver. Coordination is implicit.

### 3.2 Solution

**Disk is authoritative; cache is a read-through view that never gates visibility.** API endpoints read HIL state from disk directly.

**Driver never exits during a job's lifecycle.** It waits in-process for human submissions. No respawn coordination — submission writes the typed value to disk; the driver's wait condition flips; the driver continues.

**HIL is not a special node kind** — it's a node with `actor: human`, same shape as any artifact node. Form rendering is derived from the variable type's schema, not bespoke per stage.

**Submission verification is synchronous.** The HIL submission API runs the variable type's `produce` immediately. If `produce` raises (e.g. `pr-merge-confirmation` queries GitHub and finds the PR not actually merged), the submission is rejected with the error returned to the human. They retry. No async verification queue.

**Crash recovery is universal, not HIL-specific.** On driver (re)start, the engine scans the job's disk state — variable presence, stage states, pending markers — and resumes from there. A driver that crashed mid-await re-enters the wait condition; if the variable arrived during the crash window, the driver picks it up immediately. This is required for any kind of crash, not just HIL.

### 3.3 Explicit HIL — what the YAML looks like

```yaml
nodes:
  - id: review-design-spec
    kind: artifact
    actor: human
    after: [write-design-spec]
    inputs:
      design_spec: $design_spec
    outputs:
      verdict: { type: review-verdict }
    presentation:
      title: "Review the design spec"
      summary: "Approve or request revisions."
```

Inputs are typed variables the human reads; outputs are typed variables the human produces (form rendered from each output's type); `presentation` is dashboard listing copy. Engine handles the rest.

### 3.4 Implicit HIL — agent-initiated mid-task

An agent legitimately encounters mid-task ambiguity that the workflow author can't pre-declare. The agent invokes an MCP tool to ask the human:

```
agent → MCP tool call → MCP server creates pending item, holds response
  → dashboard renders → human answers via dashboard API
  → dashboard relays to MCP → MCP returns tool result → agent continues in-flight
```

Driver, MCP server, and agent process all stay alive across the wait. The agent's reasoning resumes from exactly where it paused — no restart, no input synthesis, no prompt re-render.

### 3.5 What unifies explicit and implicit

Both share the same disk pending marker, same dashboard rendering surface, same submission API. They differ only in:

- **Who initiates**: workflow author (explicit) vs agent at runtime (implicit).
- **Who consumes the answer**: downstream node by variable name (explicit) vs the in-flight agent's MCP tool result (implicit).

### 3.6 What this collapses (vs. today)

- HIL stitching test code reaching into `cache._scan` → tests submit via the public API, same path as humans.
- Driver respawn coordinated by the dashboard or test → no respawn; driver waits in-process.
- Per-stage UI templates hardcoded in the dashboard → form schema derived from the variable type.
- Implicit HIL flowing through a separate persistence path → unified with explicit HIL plumbing.

### 3.7 v1 limits worth naming

- No timeouts or default decisions on gates. A stale gate is operator cleanup.
- No multi-approver gates (any human submits, first wins).
- No partial submissions (a node's HIL outputs are submitted as one atomic call).
- Implicit HIL can break if a human takes hours to answer — the agent's LLM session can time out (stream connection drops). Treated as stage failure and retried. Explicit HIL is the safer pattern when long waits are expected.
- Submission verification is synchronous — if GitHub is unavailable when the human clicks "merged", the submission is rejected and they retry. We accept this because Hammock cannot make progress during a GitHub outage anyway (every push / PR depends on it).

---

## 4. Workflow validator

### 4.1 Problem

The engine catches missing / wrong / inconsistent workflow shape at runtime — naming-convention mismatches, missing producers, type confusion — and either crashes mid-job or silently falls through (the dogfood path). The workflow author has no way to know their workflow is broken until they run it.

### 4.2 Solution

A static validator that runs at workflow-load time and refuses to start a job whose workflow is structurally incoherent. Errors point at YAML lines. A validator is either correct or broken — there is no "too strict" mode and no `--allow-warnings` escape hatch. Every finding is a hard error.

### 4.3 What it checks (v1 set)

Structural integrity:

1. Every declared variable has exactly one producer (a single scalar producer, or a single producing loop body for indexed variables).
2. Every input reference resolves to a declared variable; types match.
3. The DAG has no cycles outside loop bodies.
4. Every node's `after:` references exist and don't form forward cycles.
5. Every variable type is in the closed registry; every `VariableDecl` shape matches its type's `Decl` Pydantic model.

Loop indexing (per §1.5):

6. Every variable reference inside a loop body uses `[i]`, `[i-1]`, `[k]`, or `$loop-id.index` form. References to a variable produced outside the loop use unindexed form. References outside the loop to body-produced variables use `[last]`, `[*]`, or `[k]`.
7. A required (non-`?`) input referencing `$loop-id.var[i]` must point at a producer upstream in the same-iteration body DAG.
8. A reference to `$loop-id.var[i-1]` is only legal when the consumer marks the input optional (`?`).
9. `[k]` literal references against count loops are bounds-checked at validation when `count` is statically known.

Optional / Maybe (per §1.6):

10. A required (non-`?`) input that references a `Maybe` variable must be guarded by a `runs_if` predicate referencing the same variable.

Loop control:

11. `until` loops carry a required `max_iterations` field (positive integer).
12. Loop `outputs:` block exposes only variables produced by the body; types match the projected shape (scalar for until, `list[T]` for count, `[k]` for explicit index).

Engine invariants the validator surfaces statically when possible:

13. A `code`-kind node must declare at least one typed output OR be expected to change substrate (engine cross-checks at runtime, but the validator can flag obviously empty `code` nodes).

### 4.4 What it can't catch (runtime-only)

- Whether an agent will actually produce the typed value satisfying the contract.
- Whether `gh pr create` will succeed at the moment the `pr` type tries to produce.
- Whether human submissions will arrive within session timeout.
- Whether `runs_if`-style predicates evaluate true (only known once the upstream variable exists).

These remain runtime failures; the validator's job is the structural envelope around them.

### 4.5 Where it runs

- **CLI `workflow validate <path>`** — author runs this before committing a workflow.
- **CLI `job submit`** — refuses a job whose compiled workflow doesn't validate.
- **Driver spawn** — last gate; redundant if submit validated, but the engine never trusts upstream callers.

### 4.6 Error shape

Each finding carries: file path, line number, node id, what the contract says, what was found.

Example:

```
workflow.yaml:42: node 'review-impl-spec' input 'plan' references variable '$plan'
  which has no producer (typo? expected '$impl_plan'?)
```

### 4.7 What this collapses (vs. today)

- Naming-convention mismatches caught after running 14 stages → caught at submit time.
- Silent fall-through on missing parent branches / producers → hard refusal with a pointer.
- The author iterating "run, fail, fix" loop → "validate, fix, run".

---

## 5. Loop semantics + retries

### 5.1 Loop corner cases (v1 decisions)

| Case | Behaviour |
|---|---|
| `count: 0` | Loop body runs 0 times. `[*]` produces an empty list; `[last]` produces `Maybe[T]` resolved to absent. Downstream consumers must handle empty/absent. |
| `count` is negative or non-integer | Hard runtime error; job fails with a clear message. |
| Nested loops | Supported with no depth cap. Each loop has its own ID and independent index; references are fully qualified per §1.5. |
| Iteration ordering | Sequential only. Parallel iteration deferred. |
| Iteration failure | Retry per node-level `retries.max`; if still failing, whole loop fails (no skip-and-continue). |
| `count` unresolvable at dispatch | Impossible by validator construction; runtime sanity check exits with an internal error. |
| `until` predicate never satisfied | Loop runs to `max_iterations`, then fails. `max_iterations` is a required field (no engine default). |

### 5.2 Retry primitive

Applies to any node, not loop-specific:

```yaml
nodes:
  - id: implement-fix
    kind: code
    actor: agent
    retries: { max: 2 }   # default 0
```

Semantics:

- `max: N` means up to `N+1` total attempts.
- Substrate persists across retries (worktree, branch). Engine doesn't reset; the agent can build on partial work or clean up itself.
- Per-attempt budget (`max_turns`, `max_budget_usd`) applies fresh each retry. Cumulative tracking is operator concern via the cost log.
- Each attempt creates a new attempt directory under the stage run dir (existing layout).
- Retry on every failure class in v1. Engine doesn't yet have enough information to classify "transient" vs "hard stop"; over-retrying is cheap relative to debugging a missed retry.

---

## 6. Worked example: fix-bug workflow

This is the design's acid test — the current fix-bug template re-expressed end-to-end in the new model. If the abstractions are right, this YAML reads cleanly without convention-soup or runtime guessing.

### 6.1 Shape of the workflow

```
write-bug-report
  └→ design-spec-loop  (until human approves)
       └→ design-spec-agent-loop  (until agent approves)
            ├─ write-design-spec
            └─ review-design-spec-agent
       └─ review-design-spec-human
  └→ impl-spec-loop          (mirrors design-spec block)
  └→ impl-plan-loop          (mirrors design-spec block)
  └→ implement-loop          (count-loop driven by impl_plan.count)
       └→ pr-merged-loop     (until human merges)
            ├─ implement
            └─ pr-merge-hil
  └→ tests-and-fix           (single agent node, internal test→fix loop)
  └→ tests-pr-merge-hil      (conditional: only if tests-and-fix produced a PR)
  └→ write-summary
```

### 6.2 YAML

References inside loop bodies use the strongly-typed indexing rules from §1.5: every cross-iteration reference is qualified with its loop's id and an explicit index. Outside the loop, only `[last]` / `[*]` are legal.

```yaml
workflow: fix-bug

variables:
  # job input
  job_request:                  { type: job-request }

  # produced artifacts
  bug_report:                   { type: bug-report }
  design_spec:                  { type: design-spec }
  design_spec_review_agent:     { type: review-verdict }
  design_spec_review_human:     { type: review-verdict }
  impl_spec:                    { type: impl-spec }
  impl_spec_review_agent:       { type: review-verdict }
  impl_spec_review_human:       { type: review-verdict }
  impl_plan:                    { type: impl-plan }
  impl_plan_review_agent:       { type: review-verdict }
  impl_plan_review_human:       { type: review-verdict }

  # implement loop
  pr:                           { type: pr }
  pr_review:                    { type: review-verdict }    # verdict ∈ {merged, needs-revision}
  pr_list:                      { type: list[pr] }          # outer-loop aggregate (engine-derived)

  # tests + fix
  tests_pr:                     { type: pr }                # optional output

  # final
  pr_merge_confirmation:        { type: pr-merge-confirmation }
  summary:                      { type: summary }

nodes:

  - id: write-bug-report
    kind: artifact
    actor: agent
    inputs:
      request: $job_request
    outputs:
      bug_report: $bug_report

  # ----- Design spec (until-loop wrapping until-loop) -----
  - id: design-spec-loop
    kind: loop
    until: $design-spec-loop.design_spec_review_human[i].verdict == 'approved'
    max_iterations: 3
    after: [write-bug-report]
    body:
      - id: design-spec-agent-loop
        kind: loop
        until: $design-spec-agent-loop.design_spec_review_agent[i].verdict == 'approved'
        max_iterations: 3
        body:
          - id: write-design-spec
            kind: artifact
            actor: agent
            inputs:
              bug_report: $bug_report
              prior_review?: $design-spec-agent-loop.design_spec_review_agent[i-1]
              prior_human_review?: $design-spec-loop.design_spec_review_human[i-1]
            outputs:
              design_spec: $design_spec
          - id: review-design-spec-agent
            kind: artifact
            actor: agent
            inputs:
              design_spec: $design-spec-agent-loop.design_spec[i]
            outputs:
              verdict: $design_spec_review_agent
      - id: review-design-spec-human
        kind: artifact
        actor: human
        inputs:
          design_spec: $design-spec-loop.design_spec[i]
          agent_verdict: $design-spec-loop.design_spec_review_agent[i]
        outputs:
          verdict: $design_spec_review_human
        presentation:
          title: "Review the design spec"
    outputs:
      design_spec: $design-spec-agent-loop.design_spec[last]
      design_spec_review_human: $design-spec-loop.design_spec_review_human[last]
      design_spec_review_agent: $design-spec-loop.design_spec_review_agent[last]

  # ----- Impl spec (mirrors design-spec block; same indexing rules) -----
  - id: impl-spec-loop
    kind: loop
    until: $impl-spec-loop.impl_spec_review_human[i].verdict == 'approved'
    max_iterations: 3
    after: [design-spec-loop]
    body:
      - id: impl-spec-agent-loop
        kind: loop
        until: $impl-spec-agent-loop.impl_spec_review_agent[i].verdict == 'approved'
        max_iterations: 3
        body:
          - id: write-impl-spec
            kind: artifact
            actor: agent
            inputs:
              bug_report: $bug_report
              design_spec: $design-spec-loop.design_spec[last]
              prior_review?: $impl-spec-agent-loop.impl_spec_review_agent[i-1]
              prior_human_review?: $impl-spec-loop.impl_spec_review_human[i-1]
            outputs:
              impl_spec: $impl_spec
          - id: review-impl-spec-agent
            kind: artifact
            actor: agent
            inputs:
              impl_spec: $impl-spec-agent-loop.impl_spec[i]
              design_spec: $design-spec-loop.design_spec[last]
            outputs:
              verdict: $impl_spec_review_agent
      - id: review-impl-spec-human
        kind: artifact
        actor: human
        inputs:
          impl_spec: $impl-spec-loop.impl_spec[i]
          agent_verdict: $impl-spec-loop.impl_spec_review_agent[i]
        outputs:
          verdict: $impl_spec_review_human
        presentation:
          title: "Review the impl spec"
    outputs:
      impl_spec: $impl-spec-agent-loop.impl_spec[last]

  # ----- Impl plan (mirrors design-spec block) -----
  - id: impl-plan-loop
    kind: loop
    until: $impl-plan-loop.impl_plan_review_human[i].verdict == 'approved'
    max_iterations: 3
    after: [impl-spec-loop]
    body:
      - id: impl-plan-agent-loop
        kind: loop
        until: $impl-plan-agent-loop.impl_plan_review_agent[i].verdict == 'approved'
        max_iterations: 3
        body:
          - id: write-impl-plan
            kind: artifact
            actor: agent
            inputs:
              impl_spec: $impl-spec-loop.impl_spec[last]
              prior_review?: $impl-plan-agent-loop.impl_plan_review_agent[i-1]
              prior_human_review?: $impl-plan-loop.impl_plan_review_human[i-1]
            outputs:
              impl_plan: $impl_plan
          - id: review-impl-plan-agent
            kind: artifact
            actor: agent
            inputs:
              impl_plan: $impl-plan-agent-loop.impl_plan[i]
              impl_spec: $impl-spec-loop.impl_spec[last]
            outputs:
              verdict: $impl_plan_review_agent
      - id: review-impl-plan-human
        kind: artifact
        actor: human
        inputs:
          impl_plan: $impl-plan-loop.impl_plan[i]
          agent_verdict: $impl-plan-loop.impl_plan_review_agent[i]
        outputs:
          verdict: $impl_plan_review_human
        presentation:
          title: "Review the impl plan"
    outputs:
      impl_plan: $impl-plan-agent-loop.impl_plan[last]

  # ----- Implement (count-loop containing until-merged loop) -----
  - id: implement-loop
    kind: loop
    count: $impl-plan-loop.impl_plan[last].count
    substrate: per-iteration   # each outer iteration = fresh branch off the job branch
    after: [impl-plan-loop]
    body:
      - id: pr-merged-loop
        kind: loop
        until: $pr-merged-loop.pr_review[i].verdict == 'merged'
        max_iterations: 3
        substrate: shared       # all inner retries reuse the same stage branch
        body:
          - id: implement
            kind: code
            actor: agent
            inputs:
              impl_plan: $impl-plan-loop.impl_plan[last]
              stage_index: $implement-loop.index
              prior_review?: $pr-merged-loop.pr_review[i-1]
            outputs:
              pr: $pr           # `pr` type: engine pushes + opens PR after actor exits
          - id: pr-merge-hil
            kind: artifact
            actor: human
            inputs:
              pr: $pr-merged-loop.pr[i]
            outputs:
              pr_review: $pr_review
            presentation:
              title: "Merge PR"
              summary: "Merge on GitHub. Click 'merged' once GitHub confirms; otherwise click 'needs-revision' with feedback."
        outputs:
          pr: $pr-merged-loop.pr[last]
    outputs:
      pr_list: $implement-loop.pr-merged-loop.pr[*]   # count-loop aggregates → list[pr]

  # ----- Tests + fix (single agent-run node, internal loop in the prompt) -----
  - id: tests-and-fix
    kind: code
    actor: agent
    after: [implement-loop]
    # No explicit inputs — substrate is a fresh checkout off the job branch,
    # which now has all merged PRs from implement-loop. Agent's prompt loops
    # test→fix internally; engine pushes + opens a PR via `pr` type if the
    # agent committed, else the optional output is absent.
    outputs:
      tests_pr?: $tests_pr

  # ----- Conditional HIL PR merge for any test-fix PR -----
  - id: tests-pr-merge-hil
    kind: artifact
    actor: human
    after: [tests-and-fix]
    runs_if: $tests_pr          # truthy iff tests-and-fix produced a PR
    inputs:
      pr: $tests_pr
    outputs:
      merge_confirmation: $pr_merge_confirmation
    presentation:
      title: "Merge the test-fix PR"

  # ----- Summary -----
  - id: write-summary
    kind: artifact
    actor: agent
    after: [tests-and-fix, tests-pr-merge-hil]
    inputs:
      bug_report: $bug_report
      design_spec: $design-spec-loop.design_spec[last]
      impl_spec: $impl-spec-loop.impl_spec[last]
      impl_plan: $impl-plan-loop.impl_plan[last]
      prs: $implement-loop.pr_list
      tests_pr?: $tests_pr
    outputs:
      summary: $summary
```

Address-form notes:

- Inside an agent-loop body, references to that loop's variables use `$<agent-loop-id>.var[i]` for the current iteration's value (must be upstream in the body DAG) or `[i-1]` for the prior iteration (input must be `?`).
- Cross-loop references inside an inner body use the outer loop's id explicitly: `$design-spec-loop.design_spec_review_human[i-1]` reads the previous outer iteration's human review from inside the inner agent-loop's body.
- References from one top-level node to another's loop output use `[last]` for scalars and `[*]` for lists.
- The `count` of the implement-loop is `$impl-plan-loop.impl_plan[last].count` — field access on a typed value, resolved by the engine through Pydantic introspection of the `impl-plan` `Value` schema.

### 6.3 What this proves about the design

- **No naming heuristics.** Reviewer-producer relationships are explicit (`agent_verdict: $design_spec_review_agent`); the validator can confirm them; renaming a stage doesn't break the engine.
- **No dynamic stage-list mutation.** The implement-loop's `count` reads from `$impl_plan.count` — the workflow stays a static DAG; no plan.yaml merging at runtime.
- **No PR protocol injection.** The `pr` variable type's `produce` does push + `gh pr create`; the agent's prompt is "edit and commit", nothing more.
- **No JOB_DIR vs cwd boilerplate.** Each node's kind determines the substrate the engine renders into the prompt; agents see `actor_workdir` only when they have one.
- **Conditional flow is structural.** `runs_if: $tests_pr` is an explicit predicate over a typed variable, evaluated by the engine at dispatch — not embedded in stage names or post-hoc test assertions.
- **Optional outputs collapse the tagged-union ceremony.** `tests_pr?` either exists or it doesn't; downstream gating is one truthiness check.

### 6.4 What's deliberately not here

- No retry blocks shown — every node could declare `retries: { max: N }` (§5), omitted for readability.
- No constants block — would carry workflow-wide settings like `default_max_iterations`, model selection, base branch.
- No agent-prompt templates — those live alongside the workflow in template-specific files; the YAML only references them by stage id.
- No engine-actor nodes — none are needed for fix-bug; they would appear with `actor: engine` for things like "run linter" if ever desired.

---

## 7. v1 implementation backlog

Concrete things to build, organised by surface. Each item is a unit of work that lands a specific design decision.

### Engine

- Static workflow validator (§4) running at CLI register, job submit, and driver spawn.
- Long-lived driver process that waits in-process across HIL gates (§3).
- Crash recovery: on driver (re)start, scan disk for variable + node state, resume from there (§3.2).
- Variable resolver implementing §1.5 strongly-typed indexing: `$loop-id.var[i|i-1|last|*|k]`, `$loop-id.index`, field access (`$var.field`).
- Substrate allocator (§2.4): pulls job branch before each fork; recovers missing stage branches by re-fork; per-iteration vs shared modes.
- Engine invariant: a `code`-kind node must produce at least one typed output OR change its substrate, *unless* all its declared outputs are optional. Pre/post substrate inspection.
- Persistence with engine-owned envelope: `{type, version, repo, producer_node, produced_at, value}`. Type implementations only own the `value` payload.
- Public HIL submission API: writes typed value to disk, removes pending marker, runs the type's `produce` synchronously, returns errors on verification failure (§3.2). Same path for explicit and implicit HIL.
- Disk-first HIL state (no cache gating); any cache is a derived view.
- Optional/Maybe semantics per §1.6: validator catches missing guards; `after:` treats SKIPPED == SUCCEEDED; skipped nodes produce no outputs.

### Node kinds

- `artifact` (job dir only).
- `code` (job dir + worktree + stage branch off job branch).
- `loop` (control structure with `count` or `until`, `max_iterations`, `substrate`, body sub-DAG, outputs projection).

### Variable types (closed registry, v1)

Each type = one class with `Decl` + `Value` Pydantic models + 3-4 protocol methods (~30-50 lines for simple types):

- `job-request`
- `bug-report`
- `design-spec`
- `impl-spec`
- `impl-plan` — carries `count: int` and per-iteration `stages: list[ImplPlanStage]`.
- `review-verdict` — verdict enum + summary + concerns.
- `pr` — `produce` does push + `gh pr create`; record carries url, number, branch, repo.
- `branch` — typed branch identity (name + repo).
- `pr-merge-confirmation` — `produce` queries GitHub to confirm the PR is actually merged before accepting submission.
- `summary`

Engine-derived (no per-type code):

- `list[T]` — parametric, produced automatically by count-loops.
- `Maybe[T]` — parametric, marker-driven via `?` syntax.

### Loop primitives

- `count` loop with default `substrate: per-iteration`.
- `until` loop with required `max_iterations` and default `substrate: shared`.
- Loop `outputs:` block projecting body variables (scalar for until via `[last]`, list for count via `[*]`).
- Nested loops supported with no depth cap; each loop's id qualifies its own indexing scope.

### CLI / Dashboard

- `hammock workflow validate <path>` — runs the validator standalone.
- Dashboard renders HIL forms from each variable type's `form_schema` method (default derived from `Value` Pydantic schema).
- HIL listing reads from disk on each request; no cache layer between disk and the API.
- Job dashboard surfaces typed variable state, loop iteration progress, pending HIL items.

### Workflow templates

- Re-express the bundled fix-bug template per §6.
- Drop the `plan.yaml` runtime-merge mechanism; the equivalent is a count-loop driven by `$impl-plan-loop.impl_plan[last].count`.
