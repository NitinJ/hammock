# engine/v1/

The workflow execution engine. Reads a workflow yaml, walks its DAG, dispatches each node, persists state.

## What the engine does

1. **Submit** (`driver.submit_job`) — validate workflow, create job dir, write `JobConfig`, seed `request` envelope, copy project repo for code-bearing workflows.
2. **Run** (`driver.run_job`) — topologically iterate nodes, dispatch by kind, persist `NodeRun` state per node, transition `RUNNING → COMPLETED | FAILED`.
3. **Resume** — if the driver crashes, the next `run_job` call reads `cfg.state` and skips already-`SUCCEEDED` / `SKIPPED` nodes.

## Files

| File                  | Responsibility                                                        |
|-----------------------|-----------------------------------------------------------------------|
| `driver.py`           | Top-level orchestration: topo order, state transitions, HIL gate.     |
| `driver_main.py`      | Entry point spawned by the dashboard. Calls `run_job`.                |
| `loader.py`           | YAML → `Workflow` Pydantic model. `schema_version` chokepoint.        |
| `validator.py`        | DAG correctness: cycles, undeclared variables, malformed refs.        |
| `artifact.py`         | Dispatcher for `kind: artifact, actor: agent`. Spawns claude, runs `produce`. |
| `code_dispatch.py`    | Dispatcher for `kind: code, actor: agent`. Worktree + branch + push + PR. |
| `loop_dispatch.py`    | Dispatcher for `kind: loop`. Iterates body. count vs until.           |
| `prompt.py`           | Prompt assembly: header + middle (from `.md` file) + outputs section. |
| `substrate.py`        | Job repo clone, worktrees, branch management.                         |
| `git_ops.py`          | `git push`, `gh pr create`, branch helpers.                           |
| `hil.py`              | Pending markers, wait loop for human-actor nodes.                     |
| `resolver.py`         | Variable reference resolution (`$var`, `$loop.var[i]`, `[*]` aggregate). |
| `predicate.py`        | Predicate evaluation for `runs_if` and until conditions.              |

## Three node kinds

- **`artifact`** — produces a typed envelope. Cwd = `<job_dir>/repo`. No git operations. Used for design specs, plans, reviews, summaries.
- **`code`** — produces code. Cwd = `<job_dir>/repo-worktrees/<node_id>/` on `hammock/stages/<slug>/<node_id>`. Engine pushes the branch and opens a PR via `gh` after the agent commits. Used for `implement`, `tests-and-fix`.
- **`loop`** — wraps a body sub-DAG. `count` (literal int or `$ref.field`) or `until` (predicate). Substrate per iteration is `per-iteration` (default for count) or `shared` (default for until).

Each kind has three actor variants (`agent`, `human`, `engine`). v1 uses `agent` and `human`. `engine` is reserved for engine-produced types like `request`.

## The dispatch contract

For an agent node, the dispatcher:

1. Resolves inputs via `resolver.resolve_node_inputs`.
2. Builds the prompt via `prompt.build_prompt` (or `code_dispatch._build_code_prompt` for code).
3. Persists `prompt.md` to `<job_dir>/nodes/<id>/runs/<n>/prompt.md`.
4. Spawns `claude -p <prompt>` via the injected `ClaudeRunner` callable. The runner accepts `(prompt, attempt_dir, cwd)` for artifact and `(prompt, attempt_dir, worktree)` for code. **Cwd is part of the contract.**
5. After exit, runs each declared output's `<type>.produce(decl, ctx)` to validate and serialize the typed envelope to disk.
6. Returns `DispatchResult` / `CodeDispatchResult` with `succeeded` + optional error.

Error cases:
- claude rc != 0 → `succeeded=False, error="claude subprocess failed: rc=N"`.
- output file missing → `succeeded=False, error="output contract failed: ..."` from `produce`.
- output JSON invalid → `succeeded=False`, schema error from Pydantic.

## Prompt layering

Three layers, assembled in `build_prompt` / `_build_code_prompt`:

1. **Header** (Python) — node identity, working directory, branch info (code only).
2. **Middle** (file) — `<workflow_dir>/prompts/<node_id>.md`. Required for every agent-actor node. Missing file → `FileNotFoundError`.
3. **Footer** (Python, type-driven) — for each input, `type.render_for_consumer(value, ctx)`. For each output, `type.render_for_producer(decl, ctx)`. The output section names the absolute path the agent must write to.

`workflow_dir` is threaded through `dispatch_*_agent` from the driver, derived from `Path(cfg.workflow_path).parent`.

## Substrate

Code nodes need a git worktree. `substrate.py`:

- `copy_local_repo` (at submit time): copies operator's `repo_path` → `<job_dir>/repo`, creates `hammock/jobs/<slug>` branch, pushes to origin.
- `allocate_code_substrate` (at code-node dispatch): creates `<job_dir>/repo-worktrees/<node_id>/` worktree on `hammock/stages/<slug>/<node_id>` branched off the job branch.

Loop substrates: `per-iteration` allocates a fresh worktree per iter; `shared` reuses one across iters.

## HIL flow

For `kind: artifact, actor: human`:

1. Driver: `_persist_state(BLOCKED_ON_HUMAN)`, **then** `write_pending_marker`. (Order matters — see `gotchas.md`.)
2. Driver: `wait_for_node_outputs(timeout=...)` polls for the expected envelope file.
3. Dashboard's `POST /api/hil/<slug>/<node_id>/answer` runs the type's `produce`, writes the envelope, removes the pending marker.
4. Driver wakes, transitions back to `RUNNING`.

## Variable resolution

`resolver.py` and `predicate.py` implement `$var`, `$var.field`, `$loop-id.var[i]`, `$loop-id.var[last]`, `$loop-id.var[i-1]`, `$loop-id.var[*]`. The same syntax appears in:

- `inputs:` / `outputs:` on every node (`$var.field`).
- `runs_if:` predicates.
- Loop `count: $ref.field` and `until:` predicates.
- Loop output projections (`$loop.var[*]` aggregates).

## What changes touch the engine often

| If you're changing                                       | Touch                              |
|----------------------------------------------------------|------------------------------------|
| Node-kind dispatch behaviour                             | `artifact.py` / `code_dispatch.py` |
| Variable reference syntax                                | `resolver.py` + `predicate.py`     |
| Loop semantics (count, until, indexing)                  | `loop_dispatch.py`                 |
| Prompt structure (header / footer)                       | `prompt.py` (artifact) + `code_dispatch.py:_build_code_prompt` |
| Workflow yaml schema                                     | `shared/v1/workflow.py` + `loader.py` |
| Validation rules (DAG, refs)                             | `validator.py`                     |
| HIL ordering / state transitions                         | `driver.py:_dispatch_human_node`   |

## Where to look when X happens

- Workflow won't load: `loader.py` — friendly errors for missing `schema_version`, etc.
- Job stuck in RUNNING: read `~/.hammock/jobs/<slug>/driver.log` and the most recent `nodes/<id>/runs/<n>/stderr.log`. Check `cfg.state`.
- Loop iteration not running: `loop_dispatch.py` — count/until logic + substrate planning.
- Predicate eval surprises: `predicate.py` — field walking, `[i-1]` boundary cases.

## Detail docs

For the bigger picture (HTTP layer, projections, on-disk layout): `docs/for_agents/architecture.md`.

For testing strategy and fixtures: `docs/for_agents/testing.md`.

For rules — what NOT to do: `docs/for_agents/rules.md`.

For specific footguns: `docs/for_agents/gotchas.md`.
