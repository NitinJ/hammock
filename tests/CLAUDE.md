# tests/

Four test layers. Pick the right one.

## Layout

```
tests/
├── shared/v1/           # Pydantic models, envelopes, type registry
│   ├── types/
│   │   ├── test_types.py            # T1 types
│   │   ├── test_t6_types.py         # T6 types
│   │   ├── test_types_document.py   # `document` field invariants (Stage 2)
│   │   ├── test_pr.py
│   │   └── test_pr_review_verdict.py
│   └── test_workflow.py             # Workflow schema, schema_version, kind discriminator
│
├── engine/v1/                        # engine internals
│   ├── test_artifact.py              # dispatch_artifact_agent
│   ├── test_code_dispatch.py         # dispatch_code_agent + git_ops integration
│   ├── test_loop_dispatch.py         # count + until + nested + projections
│   ├── test_driver.py                # submit, run, resume, topo order
│   ├── test_driver_hil.py            # HIL state transitions, timeout
│   ├── test_resolver.py              # variable refs
│   ├── test_predicate.py             # runs_if + until eval
│   ├── test_validator.py             # cycle detection, ref validity
│   ├── test_loader.py                # yaml load + schema_version (Stage 4)
│   ├── test_prompt.py                # header / middle / footer assembly
│   ├── test_substrate.py             # worktree allocation
│   ├── test_substrate_copy.py        # copy_local_repo
│   ├── test_git_ops.py               # push / gh helpers
│   ├── test_hil.py                   # pending markers, wait
│   └── test_hil_inputs.py            # HIL form input rendering
│
├── integration/                      # cross-component
│   ├── test_harness.py               # FakeEngine drives a workflow
│   ├── fake_engine.py                # the FakeEngine helper
│   ├── conftest.py                   # DashboardHandle fixture
│   ├── test_bundled_prompts.py       # bundled fix-bug + t1-basic shape
│   └── dashboard/
│       ├── test_dashboard_spawn.py   # POST /api/jobs end-to-end
│       ├── test_projects.py          # /api/projects register/verify/delete
│       ├── test_project_workflows.py # Stage 5 listing / verification
│       ├── test_workflow_copy.py     # Stage 6 copy
│       ├── test_hil_path_a.py        # explicit HIL flow
│       ├── test_hil_path_b_dashboard.py
│       ├── test_loop_unroll.py       # node-list rendering
│       ├── test_skipped_node.py      # runs_if SKIPPED
│       └── test_projections.py       # projection construction
│
└── e2e_v1/                           # outcome assertions on real-claude job dirs
    ├── test_outcomes.py              # assertion helpers (envelopes well-formed, etc.)
    ├── test_hil_stitcher.py          # HIL stitcher (the agent that aggregates reviews)
    └── workflows/T1.yaml … T6.yaml   # workflow yaml fixtures
```

## When to add a test at which layer

| You changed                                                | Layer                                        |
|------------------------------------------------------------|----------------------------------------------|
| Pydantic field, envelope shape, type contract              | `tests/shared/v1/`                           |
| Engine internals: dispatch, prompt, predicate, resolver    | `tests/engine/v1/`                           |
| API surface, projections, dashboard ↔ disk roundtrip       | `tests/integration/dashboard/`               |
| Multi-node orchestration without claude                    | `tests/integration/test_harness.py` (FakeEngine) |
| Real-claude end-to-end                                     | `tests/e2e_v1/` (outcome assertions on captured runs) |

The rule is: write the test at the **highest layer that's necessary**, never duplicate. A change to envelope shape AND compile resolution belongs in `integration/dashboard/`, not at every level.

## FakeEngine

`tests/integration/fake_engine.py` simulates a workflow run by writing envelopes directly via the same code paths the real engine uses. Covers state machine, projections, HIL flow, loop accounting. Does not cover prompt content or agent behaviour — for that, run real claude.

## DashboardHandle fixture

`tests/integration/conftest.py:dashboard` boots a live FastAPI app over a tmp `HAMMOCK_ROOT`. Returns an `httpx.AsyncClient` and the `root: Path`. Most dashboard tests use it.

## Fixtures: required fields

Every Pydantic ctor / yaml string / dict literal that constructs a workflow or narrative type **must** include certain fields. If you forget, validation crashes with `Field required`.

**`Workflow` requires `schema_version: 1`:**
- Pydantic ctor: `Workflow(schema_version=1, workflow="...", variables={}, nodes=[])`.
- yaml: `schema_version: 1\nworkflow: ...`.
- dict literal: `{"schema_version": 1, "workflow": "...", ...}`.

**Narrative types require `document: str` (min_length=1):**
- `BugReportValue(summary="x", document="## x\n\n...")`.
- `DesignSpecValue(title="t", overview="o", document="## D\n\n...")`.
- Same for `ImplSpecValue`, `ImplPlanValue`, `SummaryValue`.
- JSON payload: `{"summary": "x", "document": "..."}`.

If you're seeing "1 validation error for X / Field required", you forgot one of these. See `tests/shared/v1/types/test_types_document.py` for the guard test.

## Running tests

```
.venv/bin/pytest                                       # all
.venv/bin/pytest tests/integration/dashboard/          # dashboard tests
.venv/bin/pytest tests/engine/v1/test_driver_hil.py -v # one file
.venv/bin/pytest -k "test_copy_creates"                # by name
.venv/bin/pytest --tb=line -q                          # tight failure output
```

**Always run on both Python versions before pushing.** 3.13's threading characteristics differ enough to expose races (one was actually fixed because of this — see `docs/for_agents/gotchas.md`):

```
uv run --python 3.13 --with pytest --with pytest-asyncio --with pytest-timeout \
  --with hypothesis --with httpx --with anyio --with-editable . pytest -q
```

## When tests get flaky

3.13-only flake → almost certainly a TOCTOU race. The HIL `BLOCKED_ON_HUMAN` / pending-marker write order was the canonical example. Fix by analyzing observable orderings, not by adding `time.sleep`.

Watch out for:
- Filesystem mtime granularity (~1ms on most systems).
- Subprocess startup variance.
- File-watch poll timing (SSE).

## Detail docs

- `docs/for_agents/testing.md` — extended version of this file (which layer for what, FakeEngine usage).
- `docs/for_agents/development-process.md` — TDD discipline; full preflight before push.
- `docs/for_agents/gotchas.md` — what's gone wrong before, especially around test fixtures.
