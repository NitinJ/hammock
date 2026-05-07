# Rules

Hard rules. If a rule and your judgement conflict, the rule wins until you've raised it with the human.

## Do

1. **Run the FULL preflight gauntlet before every push.** The list is in `development-process.md`. Lint goes LAST, after every formatter pass. CI is not your QA — it's your safety net.
2. **One stage = one PR.** Each PR is independently mergeable. `main` is shippable after every merge.
3. **TDD red → green → refactor** for every behavioural change. The red phase confirms the failure mode is the spec.
4. **Use `Edit` and `Write`, not `sed` or `awk`,** for any file containing Python type annotations. `Path | None` breaks regex tools.
5. **Verify before claiming "done".** Run the verification command and read the output. Mock-runner success is not real-claude success — for prompt changes, run a real job.
6. **Keep the on-disk layout (`shared/v1/paths.py`) the single source of truth.** Tests assert against this; don't duplicate path-building.
7. **Treat the agent footer (engine-controlled) as the contract enforcement layer.** If you want to force agent behaviour, codify it in `render_for_producer` of the relevant type, not in a per-workflow prompt.

## Don't

1. **Don't push without a PR.** Branch, PR, wait for CI. Never push to `main`.
2. **Don't skip pyright.** Strict pyright on `shared/` and `dashboard/` is a CI gate. Run it locally — it catches everything mypy and ruff don't.
3. **Don't skip `pnpm build`.** Vite build is a CI gate. Type-check passing isn't enough; build can still fail on missing imports.
4. **Don't skip Playwright.** Vitest covers components; Playwright covers page-level flows. Both are CI gates.
5. **Don't change a test in the green phase.** If a test is wrong, finish the implementation against the original test, then file a follow-up to fix the test.
6. **Don't add features beyond the task.** No surrounding cleanup with a bug fix. No abstraction layers "in case." No backwards-compatibility shims for changes that haven't shipped.
7. **Don't write comments that explain WHAT.** Well-named identifiers do that. Only add a comment when the WHY is non-obvious.
8. **Don't reference `~/.hammock/` directly.** Use `shared/v1/paths.py` helpers. Tests pass `root=tmp_path` and depend on the path layout being centralized.
9. **Don't construct envelopes by hand in tests.** Use `make_envelope(type_name=..., producer_node=..., value_payload=...)`. Direct dict construction will skip envelope-shape validation and you'll get nonsensical error messages later.
10. **Don't bypass `submit_job`.** It's the only path that creates a job dir, validates the workflow, and seeds the request. Tests that need a partial state should use the FakeEngine fixture, not partial submit.

## Hard contracts

These are invariants the codebase relies on. Breaking them silently breaks something far away.

### Workflow yaml

- `schema_version: 1` is **required** at the top of every workflow.yaml. Loader rejects with a friendly error if missing.
- Every agent-actor node must have a corresponding `<workflow_dir>/prompts/<node_id>.md`. Workflow verification checks this; engine asserts at dispatch.
- Folder layout is fixed: `<name>/workflow.yaml + <name>/prompts/<id>.md`. Don't introduce alternate layouts.

### Narrative artifact types

These types carry a required `document: str` markdown field: `bug-report`, `design-spec`, `impl-spec`, `impl-plan`, `summary`. Pydantic min_length=1; missing or empty is rejected.

The dashboard renders `document` as the primary view; downstream agents consume it directly via `render_for_consumer`. Adding a new narrative type? Follow the same pattern.

Non-narrative types (`pr`, `review-verdict`, `pr-review-verdict`, `job-request`, `list[T]`) intentionally do not carry `document`. There's a regression test in `tests/shared/v1/types/test_types_document.py` that guards against accidentally adding it.

### Working directory

Every agent node — artifact and code — runs with cwd inside the project repo. Artifact: `<job_dir>/repo` on `hammock/jobs/<slug>`. Code: stage worktree on `hammock/stages/<slug>/<node_id>`.

This is what gives agents auto-loaded `CLAUDE.md`, codebase visibility, and the ability to verify entities exist before referencing them. **If you're tempted to set cwd to anywhere else for an agent node, stop and discuss.**

### State persistence ordering for HIL

`_dispatch_human_node` in `engine/v1/driver.py`:

```python
_persist_state(cfg, JobState.BLOCKED_ON_HUMAN, root=root)
write_pending_marker(...)
```

State first, marker second. Reverse this and you reintroduce the TOCTOU race that flaked tests on slow CI runners. See `gotchas.md`.

### Test fixture conventions

- Yaml fixtures: `schema_version: 1` first, then `workflow:`.
- Pydantic ctors: `Workflow(schema_version=1, workflow=..., variables=..., nodes=...)`.
- Dict literals: `{"schema_version": 1, "workflow": "...", ...}`.

## Agent-facing rules (in workflow prompts)

If you're authoring a workflow prompt (`.md` middle file), follow this pattern for any node that does research before producing output:

```markdown
**Phase 1 — Research.** ...

**Phase 2 — Produce the output.** Use the Write tool to write the output JSON
to the path named in the `## Outputs` section. Do **not** end the turn until
you have called Write. The job fails if the output file is missing.
```

Without this two-phase forcing, real claude can hit a natural stopping point after research and exit with empty output. See `gotchas.md` "real-claude prompts need imperative phrasing".

## What to do when a rule blocks you

If you genuinely think a rule is wrong for the situation, **flag it to the human, don't override it.** "I noticed rule X seems to conflict with Y, here's why I think it should be Z" is a fine message. Silently breaking a rule and explaining after is not.
