# shared/v1/

The contracts both engine and dashboard depend on. **Anything here is the source of truth.** Don't duplicate models elsewhere.

## What lives here

```
shared/v1/
├── workflow.py        # Workflow, ArtifactNode, CodeNode, LoopNode, schema_version
├── envelope.py        # Envelope wrapper for typed values on disk
├── job.py             # JobConfig, NodeRun, JobState, NodeRunState
├── paths.py           # Single source of truth for the on-disk layout
└── types/             # Variable type registry — one file per type
    ├── protocol.py    # NodeContext / PromptContext protocols + VariableTypeError
    ├── registry.py    # REGISTRY, get_type, known_type_names
    ├── job_request.py
    ├── bug_report.py
    ├── design_spec.py
    ├── impl_spec.py
    ├── impl_plan.py
    ├── summary.py
    ├── review_verdict.py
    ├── pr.py
    ├── pr_review_verdict.py
    └── list_wrapper.py  # list[T] support
```

## The variable type contract

Every variable type is a class implementing the protocol in `types/protocol.py`. Each type provides:

- `name: ClassVar[str]` — registry key (e.g. `"bug-report"`).
- `Decl: type[BaseModel]` — declaration model (per-variable config from the workflow yaml; usually empty).
- `Value: type[BaseModel]` — the typed payload Pydantic model.
- `produce(decl, ctx) -> Value` — read the agent's output JSON, validate against `Value`, return. Raises `VariableTypeError` on missing file / invalid JSON / schema violation.
- `render_for_producer(decl, ctx) -> str` — markdown fragment the engine includes in the prompt's outputs section. Names the absolute path the agent must write to + the JSON schema hint.
- `render_for_consumer(decl, value, ctx) -> str` — markdown fragment the engine includes in the prompt's inputs section. Inlines the typed fields + the `document` body for narrative types.
- `form_schema(decl) -> FormSchema | None` — for human-actor inputs. Returns the dashboard form schema (fields, validation), or `None` if not human-producible.

The registry (`types/registry.py`) maps `name → instance`. `get_type("bug-report")` returns the singleton instance.

## Adding a new type

1. Create `types/<name>.py` with a `class XType: name = "x"` plus `Decl`, `Value`, `produce`, `render_for_producer`, `render_for_consumer`, `form_schema`.
2. Register in `types/registry.py:REGISTRY`.
3. Decide: is it narrative (carries `document: str`) or not?
   - **Narrative**: bug-report, design-spec, impl-spec, impl-plan, summary. Include `document: str = Field(..., min_length=1)` on `Value`. Mention `document` in `_PROMPT_HINT`. Inline `value.document` in `render_for_consumer` under `#### Document`.
   - **Non-narrative**: pr, review-verdict, pr-review-verdict, job-request. No `document` field. There's a regression test in `tests/shared/v1/types/test_types_document.py` that guards against accidentally adding it.
4. Add unit tests in `tests/shared/v1/types/`.
5. Update test fixtures everywhere your type is constructed (search for `<TypeName>Value(` and `"<type-name>"` in tests).

## Workflow Pydantic schema

`workflow.py` is the source of truth for the yaml schema. Key invariants:

- `schema_version: Literal[1]` is required at the top. Loader rejects with friendly error if missing.
- Three node kinds, discriminated by `kind`: `ArtifactNode`, `CodeNode`, `LoopNode`. Discriminator means the validator routes to the right model automatically.
- `LoopNode.body` is a recursive `list[Node]` — supports nested loops.
- `extra="forbid"` everywhere — a typo in the yaml fails loud.

When you change this schema, you bump `schema_version` (currently `Literal[1]`) and write a migration note in `docs/hammock-workflow.md`.

## Envelope shape

`envelope.py:Envelope`:

```python
{
  "type": "design-spec",
  "version": "1",
  "repo": null,                    # set by `pr` type for code outputs
  "producer_node": "write-design-spec",
  "produced_at": "2026-05-07T...",
  "value": { ... typed payload ... },
}
```

`make_envelope(type_name, producer_node, value_payload)` is the only sanctioned constructor. Use it; don't build envelopes by hand.

`Envelope.model_validate_json(...)` is how everything reads them back.

## Path layout (`paths.py`)

Single source of truth for `~/.hammock/` layout. Helpers:

- `job_dir(slug, root=)` → `<root>/jobs/<slug>/`.
- `variables_dir(slug, root=)` → `<root>/jobs/<slug>/variables/`.
- `variable_envelope_path(slug, var_name, iter_path=(), root=)` → `<root>/jobs/<slug>/variables/<var_name>__<iter_token>.json` (top-level: `<var>__top.json`; loop body: `<var>__i<...>.json`).
- `node_state_path(slug, node_id, iter_path=(), root=)` → `.../nodes/<node_id>/<iter_token>/state.json`.
- `node_attempt_dir(slug, node_id, attempt, iter_path=(), root=)` → `.../nodes/<node_id>/<iter_token>/runs/<attempt>/`.
- `pending_marker_path(slug, node_id, iter_path=(), root=)` → `.../pending/<node_id>__<iter_token>.json`.
- `repo_clone_dir(slug, root=)` → `.../repo/`.
- `job_branch_name(slug)` → `hammock/jobs/<slug>`.
- `stage_branch_name(slug, node_id)` → `hammock/stages/<slug>/<node_id>`.

**Don't construct paths inline.** Add a helper here.

## Job state model

`job.py`:

- `JobConfig` — `<job_dir>/job.json`. `state`, `workflow_path`, `repo_slug`, timestamps.
- `NodeRun` — `<job_dir>/nodes/<id>/state.json`. `state`, `attempts`, `last_error`, timestamps.
- `JobState` enum: `SUBMITTED`, `RUNNING`, `BLOCKED_ON_HUMAN`, `COMPLETED`, `FAILED`.
- `NodeRunState` enum: `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `SKIPPED`.

`make_job_config(...)` and `make_node_run(...)` are the constructors. Use them.

## Atomic writes

For anything the dashboard reads (projections walk these files), write via `shared/atomic.py:atomic_write_text`. Partial writes will be observed by a concurrent `GET /api/jobs/<slug>` and produce confusing 500s.

## What changes touch this layer

| If you're changing                                      | Touch                              |
|---------------------------------------------------------|------------------------------------|
| Workflow yaml schema                                    | `workflow.py`                      |
| New variable type                                       | `types/<name>.py` + `types/registry.py` |
| Field on existing type                                  | `types/<name>.py`                  |
| Path layout                                             | `paths.py`                         |
| State enum values                                       | `job.py` (and migrate persisted state) |

## Detail docs

- `docs/for_agents/architecture.md` — how envelopes flow, prompt assembly via type renderers.
- `docs/for_agents/rules.md` — narrative type contract, `document` field rule, type registry as universal contract.
- `docs/for_agents/testing.md` — `tests/shared/v1/` layout.
