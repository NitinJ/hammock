# Hammock v2 — workflow_expander node

Status: design — implementation in flight. Extends `hammock-v2-design.md`.

## Why

Some workflows can't be fully described at definition time. The shape of the DAG depends on a runtime artifact — an implementation spec lists stages, an analysis lists per-test failures, a plan declares per-stage tasks. The operator who writes the workflow yaml doesn't know how many stages or tasks there will be. Static yaml fails them.

`workflow_expander` is a node kind whose contract is: produce a structured `expansion.yaml`. The orchestrator merges that file's contents into the runtime DAG. The agent itself authors the next slice of work.

## Schema

One new field on `Node`: `kind: agent | workflow_expander`. Default `agent` (existing behavior).

```yaml
- id: execute-impl-plan
  kind: workflow_expander
  prompt: execute-impl-plan          # the expander's own subagent prompt
  after: [write-impl-spec]
  requires: [output.md, expansion.yaml]
```

The expander's prompt is the operator's choice. The contract: the subagent must write `expansion.yaml` in addition to `output.md`. `output.md` is human-readable narrative; `expansion.yaml` is the machine-consumed plan.

## expansion.yaml shape

A subset of workflow.yaml — only the `nodes:` section, validated against the same Pydantic Workflow model.

```yaml
# nodes/execute-impl-plan/expansion.yaml
nodes:
  - id: stage-1-task-a
    prompt: implement-task
    requires: [output.md]
  - id: stage-1-task-b
    prompt: implement-task
    requires: [output.md]
  - id: stage-1-checkpoint
    prompt: stage-checkpoint
    after: [stage-1-task-a, stage-1-task-b]
    human_review: true
  - id: stage-2-task-a
    prompt: implement-task
    after: [stage-1-checkpoint]
    requires: [output.md]
  - id: stage-2-task-b
    prompt: implement-task
    after: [stage-1-checkpoint]
    requires: [output.md]
```

## Constraints (validator-enforced)

1. **No nested expanders**: every node in expansion.yaml must have `kind: agent` (default). `kind: workflow_expander` is rejected at merge time.
2. **No reaching out**: expansion's `after:` references must resolve to other nodes within the SAME expansion. Cannot point to static workflow nodes or to nodes in other expanders' expansions.
3. **No id collisions**: each expanded node's id is auto-prefixed with `<expander_id>__` before being added to the runtime DAG. Internal expansion ids stay un-prefixed in expansion.yaml so the agent can author with simple ids.
4. **Schema valid**: same Pydantic Workflow model. If invalid, the orchestrator's standard retry-on-validation-failure path applies (re-Task the expander once with a sterner instruction).

## Orchestrator behavior

When the orchestrator encounters a node with `kind: workflow_expander`:

1. Dispatch the expander's subagent via `Task` (same as any agent node), respecting `worktree`, etc.
2. After Task return:
   - Run the standard `requires:` strict-existence check (which now includes `expansion.yaml`).
   - Parse `expansion.yaml`. Validate against Workflow Pydantic model.
   - Reject if any expanded node has `kind: workflow_expander` (nesting forbidden).
   - Reject if any `after:` references a name not in the expansion.
   - Prefix every expanded id with `<expander_id>__`. Internal `after:` references inside expansion are mapped to the prefixed names.
   - Merge into the runtime DAG: each expanded node becomes an entry in `orchestrator_state.json`'s `expanded_nodes` map, keyed by prefixed id, with `parent_expander: <expander_id>` for grouping.
3. Materialize a folder for each expanded node at `nodes/<expander_id>/<child_id>/` with an initial `state.md` containing `state: pending`.
4. The orchestrator's main loop dispatches expanded nodes the same way as static nodes — they're just additional entries in the runtime DAG. Concurrency cap (10) applies as usual.
5. Static nodes downstream of the expander wait for ALL expanded children to be in a terminal state (succeeded / failed / skipped) before they can dispatch. The expander is treated as "completed" only when (a) its own Task completed, (b) expansion.yaml validated, AND (c) every expanded child reached terminal state.

## Failure modes

- **Expander Task fails**: standard retry-once policy applies. After second failure, mark expander node failed, mark job failed.
- **expansion.yaml missing or empty**: fails the strict requires check. Standard retry path.
- **expansion.yaml schema-invalid**: parse error → write `validation.md` → retry expander once with sterner instruction. After second failure, mark expander failed.
- **Expanded node fails**: that node becomes terminal-failed. Other expanded children continue. Once all expanded are terminal, downstream static nodes dispatch normally — they may run with some failed expanded children. The expander's overall state reflects this (succeeded if all expanded succeeded, failed if any did).

## Dashboard rendering

Static workflow's DagVisualizer shows the expander as a single rounded rect with an "expander" badge.

Live job timeline (left pane) shows expanded children as a collapsible group under their parent expander. Each child has its own state pill, chat tail, validation, etc. — same affordances as a top-level node.

The expander's right-pane Output tab renders its `output.md`. A new "Expansion" tab renders `expansion.yaml` as a small DAG visualizer.

## Workflow-builder agent

The builder agent's prompt gains a section: "When the user describes work whose shape depends on parsing a runtime artifact (an implementation plan, a list of bugs, a list of files to process), suggest a `workflow_expander` node. Explain the contract: the agent that runs the expander must write `expansion.yaml` describing the runtime sub-DAG."

## What this is not

- **Not a loop primitive.** No `until:`, no `count:`, no iteration semantics. Expansion is an arbitrary DAG, authored once.
- **Not nested.** No expander-of-expanders. Single-shot, single-level.
- **Not revisable.** Once expansion.yaml is merged, it's locked for this job. Re-running the job produces a fresh expansion (LLM non-determinism is operator's responsibility).
- **Not directly reviewable by operator before execution.** Auto-executes. (If operator wants a review gate, they place a `human_review: true` agent node BEFORE the expander to approve the conditions, or AFTER to approve the result.)

## Implementation cost

- Schema: +10 LOC (one discriminator, one validator extension)
- Orchestrator prompt: +50 lines (one new section + integration with the main loop)
- Dashboard projection: +30 LOC (parent_expander grouping)
- Frontend: +50 LOC (collapsible group rendering + expansion DAG mini-visualizer)
- Tests: +200 LOC (validation paths, merge logic, downstream gating)

Total: ~350 LOC delta + prompt extension. Single PR.

## Investor demo

Operator submits "fix this 5-bug regression batch" with a list of bug IDs as an attachment. The first node `triage` reads the list and emits expansion.yaml — one investigation-then-fix subgraph per bug. The dashboard timeline materializes live as the orchestrator merges the expansion. Each per-bug subgraph runs in its own worktree. After all bugs are addressed, a final `summarize` node reads every expanded child's output and writes the wrap-up.

The whole point: the agent itself authored the per-bug structure. No human wrote per-bug yaml. No static workflow expansion. The structure is shaped by what's in the input.
