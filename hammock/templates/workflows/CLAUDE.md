# hammock/templates/workflows/

Bundled workflows. Each is a folder containing a `workflow.yaml` plus a `prompts/` subdirectory with one `.md` file per agent-actor node.

## Layout

```
hammock/templates/workflows/
├── fix-bug/
│   ├── workflow.yaml
│   └── prompts/
│       ├── write-bug-report.md
│       ├── write-design-spec.md
│       ├── review-design-spec-agent.md
│       ├── write-impl-spec.md
│       ├── review-impl-spec-agent.md
│       ├── write-impl-plan.md
│       ├── review-impl-plan-agent.md
│       ├── implement.md
│       ├── tests-and-fix.md
│       └── write-summary.md
└── t1-basic/
    ├── workflow.yaml
    └── prompts/
        ├── write-bug-report.md
        ├── write-design-spec.md
        └── review-design-spec-agent.md
```

## Required shape

Every bundled workflow must:

1. **Live in a folder.** Folder name = `job_type` (the identifier the dashboard submits).
2. **Have `workflow.yaml`** at the root with `schema_version: 1` at the top.
3. **Have `prompts/<node_id>.md`** for **every agent-actor node** — both top-level and inside loop bodies. Engine errors at dispatch if any prompt is missing.

Operators copy these into their projects via the Stage 6 "Copy to project" button (or manually via `cp -R`). The copy preserves the folder structure exactly.

## The two bundled workflows

### fix-bug

The full bug-fix workflow. Agent + reviewer chain at three stages (design-spec, impl-spec, impl-plan), then a count-driven implement loop with PR-merge HIL gates per iteration, then optional tests-and-fix, then summary.

Used as the canonical real-claude target. Exercises every node kind, every actor, count loop with field-driven count, until loops with `max_iterations: 1`, optional outputs (`tests_pr?`), conditional runs_if (`runs_if: $tests_pr`).

### t1-basic

Minimal three-node workflow: `write-bug-report → write-design-spec → review-design-spec-agent`. Exists to test bundled-workflow plumbing without the cost of running fix-bug.

## Editing rules

When you change anything here:

1. The yaml must still validate: `schema_version: 1`, all node ids unique, `after:` edges form a DAG, every `$ref` resolves to a declared variable.
2. Every agent-actor node must have a corresponding `prompts/<id>.md`. If you add a node, add the prompt.
3. Run `tests/integration/test_bundled_prompts.py` to catch missing files.
4. If you change the agent-facing prompt structure (e.g. add a "Phase 1 / Phase 2" section), apply it consistently across all bundled prompts — the inconsistency itself is a bug source.

## Prompt authoring patterns

Per `docs/for_agents/gotchas.md` "real-claude prompts need imperative phrasing": for any node where the agent does substantial research before producing output, structure the middle prompt as:

```markdown
**Phase 1 — Research.** ...

**Phase 2 — Produce the output.** Use the Write tool to write the output JSON
to the path named in the `## Outputs` section. Do **not** end the turn until
you have called Write. The job fails if the output file is missing.
```

This forces the agent past the natural "I've thought about it enough" stopping point.

The bundled `write-design-spec.md` had the descriptive (not imperative) pattern in the first dogfood run and exited silently. See `docs/for_agents/memory.md` "Open followups" — there's a planned rewrite.

## What the engine reads

For each agent-actor node, the engine reads `prompts/<node_id>.md` at dispatch time and inserts it as the **middle layer** of the prompt:

```
[engine header]
## Task
<contents of prompts/<node_id>.md>
## Inputs
<rendered by each input type's render_for_consumer>
## Outputs
<rendered by each output type's render_for_producer>
```

The middle is the *only* layer that's customizable per workflow. Header and footer are engine-controlled.

## Testing bundled changes

The integration test `tests/integration/test_bundled_prompts.py` walks each bundled workflow's DAG and asserts a `prompts/<id>.md` exists for every agent-actor node. Run this test (it's part of the standard pytest run) after editing.

## Detail docs

- `docs/hammock-workflow.md` — the design doc for the workflow customization model. Read this for the why.
- `docs/for_agents/architecture.md` — prompt assembly mechanism, working directory rule.
- `docs/for_agents/rules.md` — narrative type contract, schema_version requirement.
- `docs/for_agents/gotchas.md` — empty-stdout failure mode, prompt-tuning patterns.
