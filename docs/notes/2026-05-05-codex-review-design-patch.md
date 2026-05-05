# Codex adversarial review — Hammock design patch

Captured 2026-05-05. Source: `docs/hammock-design-patch.md`.

The brief given to codex: poke at fundamentals, find places that will break under stress, force a rewrite within 2 months, or fail to deliver on the stated goal. Find the 4-7 hardest holes. Return concrete scenarios, not abstract worries.

Boundaries we told codex about (so it doesn't argue against decisions already taken):

- Closed-set type registry; no plugin extensibility in v1.
- Single long-lived driver per job.
- One-phase `produce`; rollback gaps for side effects accepted as v1 limit.
- Cross-output invariants are workflow author's problem, not engine's.
- KISS over generality; this is for the user + cofounder, not public.

---

## 1. Loop scoping breaks single-producer SSA — HIGH

**The hole.** The worked YAML cannot be validated under the stated variable model. `design_spec`, `impl_spec`, `impl_plan`, `pr`, and review verdicts are repeatedly produced inside until-loops, then projected outward with the same names. In `design-spec-loop`, every iteration of `write-design-spec` writes `design_spec: $design_spec`; the nested agent loop also writes `design_spec_review_agent` repeatedly. In `implement-loop`, each count iteration contains an until-loop that writes `pr: $pr`, then the outer loop claims `pr_list: $pr`.

**Why the patch fails.** The model says "No mutable shared state: the only writes are to declared output variables, owned by a single producer node" and the validator checks "Every declared variable has exactly one producer." But the example uses:

```yaml
- id: write-design-spec
  outputs:
    design_spec: $design_spec
...
outputs:
  pr_list: $pr              # count-loop aggregates body's $pr into list-of-pr
```

There is no formal loop frame, per-iteration variable identity, final-value projection, or aggregation rule strong enough to make this SSA.

**Repair.** Introduce explicit scoped variable names: body outputs are local to a loop frame, and loop `outputs:` maps local names to outer names with an aggregation operator:

```yaml
outputs:
  design_spec: { from: write-design-spec.design_spec, mode: final }
  pr_list: { from: pr-merged-loop.pr, mode: list }
```

Validator should reject direct writes to an outer scalar from inside a repeatable body.

---

## 2. Nested loops are both required and rejected — HIGH

**The hole.** The design's "acid test" requires nested loops for design approval, impl approval, and PR merge retry. But §5.1 says nested loops are rejected in v1. §7 then partially reverses that with "Validator rejects nested loops not part of explicit body declarations (but allows them when the body itself is a loop)." That is not a semantics; it is an exception-shaped escape hatch.

**Why the patch fails.** These constructs are central, not edge cases:

```yaml
- id: design-spec-loop
  kind: loop
  body:
    - id: design-spec-agent-loop
      kind: loop
```

and:

```yaml
- id: implement-loop
  kind: loop
  count: $impl_plan.count
  substrate: per-iteration
  body:
    - id: pr-merged-loop
      kind: loop
      substrate: shared
```

The document simultaneously says "Nested loops | Validator rejects in v1" and uses nested loops to prove the design.

**Repair.** Make nested loops a v1 requirement, but only with explicit loop frames. Define loop IDs as scopes, define which outer variables are readable, define whether body variables are `final`, `list`, or hidden, and cap nesting support to these cases. Drop the fake ban.

---

## 3. Substrate inheritance corrupts branches under nested loops — HIGH

**The hole.** `implement-loop` says each iteration gets a fresh branch off main, while inner `pr-merged-loop` says retries reuse the same branch. But after a human merges a PR, what is the next outer iteration based on: original main, current remote main, job branch, or previous iteration's merged head? The example assumes all merged PRs are now on main before the test stage — an assumption that is outside the DAG.

**Why the patch fails.** §7 says only "Substrate allocator driven by node kind and loop substrate field; nested loops inherit unless re-declared." Inheritance is undefined once there are more than two levels, or when an inner loop performs an external merge. The `pr-merge-hil` node is `kind: artifact`, so it has no repo handle, yet its semantic effect is to advance the code base.

**Repair.** Define substrate frames with explicit base policies: `fresh_from: remote_main_after(pr_merge_confirmation)` or `fresh_from: parent_iteration_head`. Treat merge confirmation as a repo-affecting engine observation that updates the parent frame's base revision. Validate that downstream "fresh off main" nodes depend on merge confirmations, not just on PR variables.

---

## 4. Variable type protocol is not enough to make "one class per type" true — MEDIUM

**The hole.** Adding a type requires more than `produce`, prompt rendering, and serialization. The design already needs: declaration validation, Pydantic schema generation, HIL form schema, field access in predicates (`$impl_plan.count`, `$pr_review.verdict`), truthiness (`runs_if: $tests_pr`), optional/list composition, UI rendering, external verification, version migration, and probably cleanup semantics for side-effecting values.

**Why the patch fails.** §1.4 defines only:

```python
produce(...)
render_for_producer(...)
render_for_consumer(...)
serialize(...)
deserialize(...)
```

But later §7 requires "Dashboard renders HIL forms from variable type's form schema," `runs_if: $tests_pr` truthiness, `$impl_plan.count` field access, `list[T]`, and `pr-merge-confirmation` querying GitHub. None of those interfaces exist in the protocol. Also, the closed set claim does not hold: the design first lists `pr`, `branch`, `review-verdict`, `integration-test-report` "plus structured-file types as needed," but the example needs `job-request`, `bug-report`, `design-spec`, `impl-spec`, `impl-plan`, `summary`, `pr-merge-confirmation`, and `list[T]` before v1 ships.

**Repair.** Expand the protocol before writing a single type class:

```python
validate_decl()
json_schema()
form_schema()
expression_fields()
truthiness()
compose_optional()
compose_list()
cleanup_plan()
```

---

## 5. Optional outputs do not compose with validation or transitive flow — MEDIUM

**The hole.** `tests_pr?` may not exist, but downstream nodes both gate on it and depend on it. `write-summary` also waits on `tests-pr-merge-hil`, which may be skipped. The model says inputs must be present before dispatch, but optional inputs intentionally violate that.

**Why the patch fails.** The YAML says:

```yaml
outputs:
  tests_pr?: $tests_pr
...
runs_if: $tests_pr
inputs:
  pr: $tests_pr
...
after: [tests-and-fix, tests-pr-merge-hil]
inputs:
  tests_pr?: $tests_pr
```

§4.4 punts predicate evaluation to runtime, but the validator still needs to know that `$tests_pr` is `Maybe[pr]`, that `tests-pr-merge-hil.pr` is guarded by presence, and that `write-summary` can proceed if the merge HIL is skipped.

**Repair.** Make optionality part of the type system: `Maybe[pr]`, presence predicates, and guard narrowing. Require every non-optional input from a maybe variable to be dominated by a `runs_if: present($tests_pr)` guard. Define `after` semantics over `SUCCEEDED | SKIPPED`. Require skipped nodes to produce no outputs — no special-casing downstream.

---

## 6. HIL submission verification is not durable — MEDIUM

**The hole.** `pr-merge-confirmation` queries GitHub before accepting the human's "merged" answer. If GitHub is down at submission time, the human cannot submit a truthful answer. If the long-lived driver crashes mid-await, the "wake" event is lost even though the answer may be on disk.

**Why the patch fails.** §3 says "Driver never exits during a job's lifecycle" and "submission writes the typed value to disk; the driver's wait condition flips." §7 says `pr-merge-confirmation` "queries GitHub to confirm the PR is actually merged before accepting human's 'merged' submission." This couples API submission to external availability and assumes process liveness.

**Repair.** Make HIL submission idempotently persist an unverified answer. A separate engine verification step transitions it to verified when GitHub is reachable. Driver resume should scan durable job state for pending/runnable nodes on startup; wakeups are hints, not correctness requirements.

---

## 7. Loop is a third node kind, despite the two-kind claim — LOW

**The hole.** §2 claims the v1 taxonomy is exactly `artifact` and `code`, but the YAML and backlog use `kind: loop` with no actor. That breaks the actor/kind matrix and the substrate contract.

**Why the patch fails.** The document says "That is the entire taxonomy," then later:

```yaml
- id: implement-loop
  kind: loop
```

and §7 lists "`loop` (control structure...)." This is not cosmetic; loop nodes have different validation, dispatch, outputs, and substrate semantics from both artifact and code nodes.

**Repair.** Admit a discriminated union: `ArtifactNode`, `CodeNode`, `LoopNode`. Loop nodes have no actor and do not run; they compile a scoped sub-DAG. Update §2's taxonomy table to include them.

---

## Codex's "one thing to fix before any code is written"

Formalize loop frames and variable projection. Every other HIGH finding cascades from the same missing core: what does a variable *mean* inside a loop body, across iterations, and after the loop exits? Without a formal loop frame — scoped namespaces, aggregation operators (`final`, `list`, `any`), and frame-to-frame base revision semantics — the single-producer SSA guarantee is fiction, the nested-loop semantics are a table footnote, the substrate inheritance rule is undefined past two levels, and the validator cannot correctly check the worked example's own YAML. Fixing this one thing does not require rethinking the rest of the design; it requires writing four pages that the design currently skips over.
