# Testing

Hammock has four test layers. Pick the right one for what you're testing.

## Layout

```
tests/
├── shared/v1/                       # Pydantic types, envelope shape, paths
│   ├── types/
│   │   ├── test_types.py            # T1 types (job-request, bug-report, design-spec, review-verdict)
│   │   ├── test_t6_types.py         # T6 (impl-spec, impl-plan, summary)
│   │   ├── test_types_document.py   # `document` field invariants per Stage 2
│   │   ├── test_pr.py
│   │   └── test_pr_review_verdict.py
│   └── test_workflow.py             # Workflow Pydantic schema
│
├── engine/v1/                       # engine-internal: driver, dispatchers, prompt, etc.
│   ├── test_artifact.py
│   ├── test_code_dispatch.py
│   ├── test_loop_dispatch.py
│   ├── test_driver.py
│   ├── test_driver_hil.py
│   ├── test_resolver.py
│   ├── test_predicate.py
│   ├── test_validator.py
│   ├── test_loader.py
│   ├── test_prompt.py
│   ├── test_substrate.py
│   ├── test_substrate_copy.py
│   ├── test_git_ops.py
│   ├── test_hil.py
│   └── test_hil_inputs.py
│
├── integration/                     # cross-component
│   ├── test_harness.py              # FakeEngine drives a workflow without claude
│   ├── fake_engine.py               # the FakeEngine fixture itself
│   ├── conftest.py                  # DashboardHandle fixture (live FastAPI + tmpdir HAMMOCK_ROOT)
│   ├── test_bundled_prompts.py      # bundled fix-bug + t1-basic ship correctly
│   └── dashboard/
│       ├── test_dashboard_spawn.py
│       ├── test_projects.py
│       ├── test_project_workflows.py
│       ├── test_workflow_copy.py
│       ├── test_hil_path_a.py
│       ├── test_hil_path_b_dashboard.py
│       ├── test_loop_unroll.py
│       ├── test_skipped_node.py
│       └── test_projections.py
│
└── e2e_v1/                          # outcome assertions on real-claude output dirs
    ├── test_outcomes.py
    ├── test_hil_stitcher.py
    └── workflows/T1.yaml … T6.yaml
```

## Which layer for what

| You changed                                                | Test at layer       |
|------------------------------------------------------------|---------------------|
| Pydantic model field, envelope shape, type contract        | `tests/shared/v1/`  |
| Engine logic: dispatch, prompt assembly, predicate eval    | `tests/engine/v1/`  |
| HTTP API surface, projections, dashboard ↔ on-disk state   | `tests/integration/dashboard/` |
| Multi-node orchestration without real claude               | `tests/integration/test_harness.py` (FakeEngine) |
| Real-claude end-to-end (post-merge dogfood)                | manual via `scripts/run-hammock.sh` |

If a behavioural change crosses layers, write the test at the **highest layer that's necessary**. A change to envelope shape *and* compile resolution belongs in `integration/dashboard/` — not duplicated at every level.

## FakeEngine

`tests/integration/fake_engine.py` simulates a workflow run without spawning Claude. It writes envelopes directly to disk via the same code paths the real engine uses. Use it when the test cares about:

- Driver state machine (SUBMITTED → RUNNING → COMPLETED).
- Projections (job listing, node detail, HIL queue rendering).
- HIL flow (pending markers, `BLOCKED_ON_HUMAN`, submit roundtrip).
- Loop iteration accounting.

It does **not** test:

- Whether a real Claude agent will actually produce the right envelope.
- Whether the prompt the engine sends has the right wording.
- Whether the agent obeys the contract under unusual inputs.

For those, use `e2e_v1/` (outcome assertions on captured real-claude job dirs) or run a real-claude job manually.

## DashboardHandle fixture

`tests/integration/conftest.py:dashboard` is the workhorse for HTTP tests. It boots a live FastAPI app over a tmp `HAMMOCK_ROOT`, returns an `httpx.AsyncClient` and the `root: Path`. Example:

```python
@pytest.mark.asyncio
async def test_something(dashboard: DashboardHandle, tmp_path_factory):
    src = _init_repo(tmp_path_factory.mktemp("p"), "myapp")
    resp = await dashboard.client.post("/api/projects", json={"path": str(src)})
    ...
    pj_on_disk = dashboard.root / "projects" / "myapp" / "project.json"
```

`_init_repo` (in `test_projects.py` / `test_project_workflows.py`) bootstraps a git checkout the verify pipeline accepts.

## Running tests

```
.venv/bin/pytest                                              # all
.venv/bin/pytest tests/integration/dashboard/                 # dashboard only
.venv/bin/pytest tests/engine/v1/test_driver_hil.py -v        # one file
.venv/bin/pytest -k "test_copy_creates"                       # by name
.venv/bin/pytest --tb=line -q                                 # tight failure output
```

Run on **both** Python versions before pushing — 3.13's threading characteristics differ from 3.12 enough to expose races. The 3.13 invocation:

```
uv run --python 3.13 --with pytest --with pytest-asyncio --with pytest-timeout \
  --with hypothesis --with httpx --with anyio --with-editable . pytest -q
```

## Frontend tests

```
cd dashboard/frontend
pnpm test                # vitest (tests/unit/)
pnpm test:e2e            # playwright (tests/e2e/)
pnpm type-check          # vue-tsc, separate from test
```

Vitest covers components in isolation (`AskHumanDisplay`, `FormRenderer`, `EnvelopeView`, `renderRows`). Playwright covers the page level (HIL form submit, two-pane navigation) against a live dashboard backend.

## Real-claude verification

Mock-runner tests don't catch prompt-tuning issues — claude can run a research plan, then exit cleanly without ever calling the Write tool. The only way to catch this class of bug is to run a real job. Do this:

1. After Stage N's PR merges, run `scripts/run-hammock.sh`.
2. Submit a tiny end-to-end job (one bug, one file, one PR-sized change).
3. Drive the HIL gates from the dashboard.
4. If anything misbehaves — empty output, wrong scope, hallucinated symbols — that's a real-claude prompt-tuning bug. File a follow-up.

`docs/for_agents/gotchas.md` has a list of failure modes seen in dogfood. Add new ones as you find them.

## Test fixtures and the `document` field

Every payload that constructs a narrative type (`bug-report`, `design-spec`, `impl-spec`, `impl-plan`, `summary`) **must** include a `document` field. Fixtures missing it fail validation since Stage 2. If you're adding a new test that touches these types:

- `BugReportValue(summary="x", document="## x\n\n...")` — Pydantic ctor.
- `{"summary": "x", "document": "..."}` — JSON payload / dict literal.
- `summary: x\ndocument: "..."` — yaml.

If you forget, you'll get `ValidationError: Field required`. Just add it.

## Test fixtures and `schema_version`

Every workflow yaml — bundled, project-local, or in a test fixture — needs `schema_version: 1` at the top. Same rule for `Workflow(...)` Pydantic constructors and `Workflow.model_validate({...})` calls. Loader rejects with a friendly message if missing; tests crash if you forget. Just add it.
