# Architecture

This is the runbook view: how a job actually flows through the system, file by file.

## The big picture

```
operator
   │
   │  POST /api/jobs (project_slug, job_type, request_text)
   ▼
┌──────────────────────────┐
│  dashboard/api/jobs.py   │  → compile.compile_job → engine.driver.submit_job
│                          │  → spawn_driver (subprocess: python -m engine.v1.driver_main <slug>)
└──────────┬───────────────┘
           │
           │  job dir on disk: ~/.hammock/jobs/<slug>/
           │     job.json (JobConfig)
           │     variables/<var_name>.json (typed envelopes)
           │     nodes/<node_id>/state.json + runs/<n>/
           │     repo/ (project clone, on hammock/jobs/<slug>)
           │     repo-worktrees/<node_id>/ (per-stage)
           │     pending/<node_id>.json (HIL markers)
           ▼
┌──────────────────────────────────────────────┐
│  engine/v1/driver.py — run_job              │
│   1. load workflow from cfg.workflow_path    │
│   2. topological order over `after:` edges   │
│   3. for each node: dispatch by kind         │
│         artifact → engine/v1/artifact.py     │
│         code     → engine/v1/code_dispatch.py│
│         loop     → engine/v1/loop_dispatch.py│
│   4. persist node state.json after each      │
│   5. transition COMPLETED / FAILED           │
└──────────────────────────────────────────────┘
```

The dashboard is a thin HTTP shell over the on-disk job tree. It never holds workflow state in memory; it projects it from `~/.hammock/` per request (see `dashboard/state/projections.py`).

## Job submit, end to end

`compile_job` in `dashboard/compiler/compile.py`:

1. Resolve workflow path: project-local first (`<repo>/.hammock/workflows/<job_type>/workflow.yaml`), then bundled (`hammock/templates/workflows/<job_type>/workflow.yaml`). See `_resolve_project_local_workflow` and `_resolve_bundled_workflow`.
2. If `dry_run`, just validate and return. Otherwise:
3. Resolve repo identity from `~/.hammock/projects/<project_slug>/project.json`.
4. Call `engine.v1.driver.submit_job` which:
   - Validates the workflow against `engine/v1/validator.py`.
   - Creates the job dir layout (`shared/v1/paths.ensure_job_layout`).
   - Writes `JobConfig` with `state=SUBMITTED`.
   - Seeds the `request` job-request envelope.
   - For workflows with code-kind nodes, copies `project.repo_path` into `<job_dir>/repo` (`copy_local_repo` in `engine/v1/substrate.py`), creates `hammock/jobs/<slug>` branch, pushes.
5. `dashboard/api/jobs.py:spawn_driver` spawns `python -m engine.v1.driver_main <slug>` as a subprocess. Job state persists across restarts; the driver reads `cfg.state` and resumes if it crashed.

## Node dispatch

The driver topologically walks `Workflow.nodes` by `after:` edges (see `_topological_order`). For each node:

| kind        | dispatcher                          | substrate                                   |
|-------------|-------------------------------------|---------------------------------------------|
| `artifact`  | `engine/v1/artifact.py`             | none — just the job dir                     |
| `code`      | `engine/v1/code_dispatch.py`        | git worktree on `hammock/stages/<slug>/<id>` |
| `loop`      | `engine/v1/loop_dispatch.py`        | recurses into body; iterations indexed      |

Within a kind, the actor (`agent` / `human` / `engine`) determines what runs:

- `agent`: spawn `claude -p <prompt>` with cwd inside the project repo.
- `human`: write a pending marker, transition to `BLOCKED_ON_HUMAN`, poll until the dashboard's `/api/hil/.../answer` lands the typed value on disk (see `engine/v1/hil.py`).
- `engine`: not used in v1 (reserved for engine-produced types like `request`).

## Prompt assembly (agent nodes)

Three layers in `engine/v1/prompt.py:build_prompt` (and `code_dispatch._build_code_prompt` for code nodes):

1. **Header** (engine-controlled): node identity, working directory hint, for code nodes the stage branch name.
2. **Middle** (loaded from `<workflow_dir>/prompts/<node_id>.md`): the per-node task instruction. Customizable per workflow.
3. **Footer** (engine-controlled, type-driven): for each input slot, render via `<type>.render_for_consumer(value, ctx)`. For each output slot, render via `<type>.render_for_producer(decl, ctx)` — this includes the absolute output path the agent must write to and the JSON schema hint.

The middle is **required** for every agent-actor node — missing file = `FileNotFoundError` at dispatch time. Workflow verification (`dashboard/api/project_workflows._verify_workflow_folder`) enforces this at project register / re-verify time, but the engine asserts the invariant defensively at dispatch.

## Working directory rule

Every agent node — artifact and code — runs with cwd inside the project repo:

- **Artifact nodes**: `cwd = <job_dir>/repo` (the job's clone, on `hammock/jobs/<slug>`).
- **Code nodes**: `cwd = <job_dir>/repo-worktrees/<node_id>/` (a worktree on the stage branch).

This is what gives agents auto-loaded `CLAUDE.md`, `Grep`/`Read` over the codebase, and groundable design specs. See `engine/v1/artifact.py:_default_claude_runner` and `code_dispatch._default_claude_runner`.

## Variable resolution

Inputs are declared in YAML as `name: $variable_reference`. The reference can be:

- `$var` — read the workflow-level variable.
- `$var.field` — walk into the typed model.
- `$loop-id.var[i]` / `$loop-id.var[last]` / `$loop-id.var[i-1]` — loop-indexed.
- `$loop-id.var[*]` — aggregate across all iterations into `list[T]`.

Resolution lives in `engine/v1/resolver.py`. Predicates (`runs_if`, until conditions) are evaluated by `engine/v1/predicate.py` with the same reference syntax.

## Envelope on disk

Every output is wrapped in an `Envelope` (Pydantic, `shared/v1/envelope.py`):

```json
{
  "type": "design-spec",
  "version": "1",
  "repo": null,
  "producer_node": "write-design-spec",
  "produced_at": "2026-05-07T14:00:00Z",
  "value": { ... typed payload ... }
}
```

Path layout (single source of truth: `shared/v1/paths.py`):

- Top-level: `<job_dir>/variables/<var_name>.json`
- Loop-indexed: `<job_dir>/variables/loop_<loop_id>_<var_name>_<iter>.json`

For narrative artifact types (`bug-report`, `design-spec`, `impl-spec`, `impl-plan`, `summary`), the value carries a `document: str` markdown field alongside the structured fields. The dashboard renders `document` as the primary view; downstream agents consume it directly.

## Loop dispatch

`LoopNode` has either `count` or `until`:

- `count`: literal int or `$ref.field` (e.g. `$impl-plan.impl_plan[last].count`). Runs body that many times, indexed `0..count-1`.
- `until`: predicate evaluated after each iteration. Capped at `max_iterations`.

Each iteration writes envelopes at the loop-indexed path. Loop output projections aggregate or pick:

- `outputs: bug_report: $body-loop.bug_report[last]` — final iteration value.
- `outputs: pr_list: $body-loop.pr[*]` — `list[pr]` aggregate.

Substrate per loop:

- `count` loops default to `per-iteration` (fresh worktree each iter).
- `until` loops default to `shared` (reuse one worktree across iters).

Override via `substrate: per-iteration | shared`.

## HIL flow

Human-actor artifact nodes block the driver:

1. Driver calls `engine.v1.hil.write_pending_marker` → `<job_dir>/pending/<node_id>.json`.
2. Driver calls `_persist_state(BLOCKED_ON_HUMAN)`. **This order matters** — see `gotchas.md` for the race that got fixed.
3. Driver calls `wait_for_node_outputs` which polls `<job_dir>/variables/` for the expected envelopes.
4. Operator submits via `POST /api/hil/<slug>/<node_id>/answer`. The dashboard runs the type's `produce` synchronously to validate, writes the envelope, removes the pending marker.
5. Driver wakes, transitions `BLOCKED_ON_HUMAN` → `RUNNING`, continues.

Implicit HIL (Claude calling the `ask_human` MCP tool) is a separate path; see `dashboard/api/hil.py` for the `/asks/<call_id>` endpoints.

## SSE pipeline

Live updates stream from `dashboard/api/sse.py`. The dashboard watches the job dir tree (file mtime polling) and emits `node_state_changed`, `envelope_written`, `pending_added`, `pending_removed` events. Frontend invalidates vue-query caches on each event so the UI reflects engine state without polling.

## Per-job MCP server

Each running job has its own MCP server spawned by the dashboard so the agent can call `ask_human` (and other future tools) scoped to its own job. See `dashboard/api/jobs.py:spawn_mcp_server`.

## File-on-disk layout (canonical)

```
~/.hammock/
├── projects/<slug>/project.json
└── jobs/<slug>/
    ├── job.json                          # JobConfig (state, workflow_path, repo_slug)
    ├── job-driver.pid                    # for resume / kill
    ├── driver.log
    ├── variables/
    │   ├── request.json                  # job-request envelope
    │   ├── bug_report.json               # typed envelope
    │   └── loop_<id>_<var>_<iter>.json   # loop-indexed
    ├── nodes/<node_id>/
    │   ├── state.json                    # NodeRun (state, attempts, last_error)
    │   └── runs/<n>/
    │       ├── prompt.md                 # exact prompt sent to claude
    │       ├── stdout.log
    │       └── stderr.log
    ├── pending/<node_id>.json            # HIL markers
    ├── repo/                             # project clone, on hammock/jobs/<slug>
    └── repo-worktrees/<node_id>/         # per-code-node, on hammock/stages/.../<id>
```

This layout is the contract. The dashboard's projections read this tree directly; the engine writes it; tests assert against it.

## Where to look when X happens

- Job stuck in `RUNNING`: `~/.hammock/jobs/<slug>/driver.log` and the most recent `nodes/<id>/runs/<n>/stderr.log`.
- HIL gate not advancing: `~/.hammock/jobs/<slug>/pending/<id>.json` exists? `cfg.state` is `BLOCKED_ON_HUMAN`?
- Node failed silently with no output: see `gotchas.md` "empty stdout from claude".
- Workflow won't load: `engine/v1/loader.py` rejects with file path + reason. Check `schema_version: 1` is present and `prompts/<id>.md` exists for every agent-actor node.
