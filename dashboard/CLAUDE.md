# dashboard/

The HTTP service. FastAPI + projections from on-disk state. No DB.

## What the dashboard does

1. Serves the SPA (`dashboard/frontend/dist/` after build).
2. Exposes `/api/...` REST endpoints + `/sse/...` SSE streams.
3. Compiles + spawns engine drivers on `POST /api/jobs`.
4. Validates and lands HIL submissions on `POST /api/hil/.../answer`.

## Layout

```
dashboard/
├── api/                     # FastAPI routers
│   ├── __init__.py          # router aggregation, /api/health, /api/workflows
│   ├── jobs.py              # POST /api/jobs (compile + spawn driver)
│   ├── hil.py               # /api/hil/... HIL queue + answer
│   ├── projects.py          # /api/projects (register/verify/delete) + workflow endpoints
│   ├── project_workflows.py # discovery + verification (Stage 5/6)
│   ├── settings.py
│   └── sse.py               # SSE event streams
├── compiler/
│   └── compile.py           # workflow_path resolution + submit_job + spawn_driver
├── state/
│   ├── projections.py       # ProjectListItem, ProjectDetail, JobDetail, NodeListEntry, etc.
│   ├── job_index.py
│   ├── nodes.py
│   ├── hil_queue.py
│   └── ...                  # all "read disk → return Pydantic projection"
├── runner/
│   └── ...                  # FakeStageRunner for HAMMOCK_FAKE_FIXTURES_DIR mode
├── settings.py              # AppSettings (root, runner_mode, claude_binary, etc.)
└── main.py                  # FastAPI app factory + middleware
```

## Key invariants

- **No in-memory state.** Every endpoint reads from `~/.hammock/` via `dashboard/state/projections.py`. No cache. No DB.
- **One driver subprocess per job.** Submitted jobs spawn `python -m engine.v1.driver_main <slug>`. Tracked by PID file at `<job_dir>/job-driver.pid`. Restart-tolerant (driver re-reads `cfg.state`).
- **Atomic writes.** Anything the dashboard reads (projections) goes through `shared.atomic.atomic_write_text`. Partial writes would be observable.
- **Path layout = `shared/v1/paths.py`.** Don't construct `~/.hammock/...` strings inline; use the helpers.

## API surface (v1)

```
GET  /api/health
GET  /api/workflows                                  (bundled, project-agnostic)
GET  /api/projects
POST /api/projects                                   (register)
GET  /api/projects/{slug}
DELETE /api/projects/{slug}
POST /api/projects/{slug}/verify                     (re-verify)
GET  /api/projects/{slug}/workflows                  (bundled + project-local)
POST /api/projects/{slug}/workflows/copy             (Stage 6)
GET  /api/jobs
POST /api/jobs                                       (submit)
GET  /api/jobs/{slug}
GET  /api/jobs/{slug}/nodes/{node_id}
GET  /api/hil
GET  /api/hil/{slug}
GET  /api/hil/{slug}/{node_id}
POST /api/hil/{slug}/{node_id}/answer
GET  /api/hil/{slug}/asks/{call_id}
POST /api/hil/{slug}/asks/{call_id}/answer
GET  /api/settings
GET  /sse/global
GET  /sse/job/{slug}
GET  /sse/node/{slug}/{node_id}
```

## Compile flow

`compile.compile_job` is the entry from `POST /api/jobs`:

1. Resolve `workflow_path`: project-local (`<repo>/.hammock/workflows/<job_type>/workflow.yaml`) **before** bundled (`hammock/templates/workflows/<job_type>/workflow.yaml`).
2. If `dry_run`, validate and return.
3. Else: resolve repo identity (`project.json` → `repo_slug`, `repo_path`, `default_branch`).
4. Call `engine.v1.driver.submit_job` — creates job dir, copies repo for code-bearing workflows.
5. Spawn driver subprocess via `dashboard/api/jobs.py:spawn_driver`.

## Project-local workflow handling (Stage 5)

`project_workflows.py` provides:

- `list_workflows_for_project(root, slug)` — bundled + `<repo>/.hammock/workflows/`. Custom shadows bundled when names collide. Each entry carries `valid: bool` and `error: str | None`.
- `verify_workflow_folder(folder)` — load yaml, check `schema_version`, check every agent-actor node has a `prompts/<id>.md`. Errors are surfaced in the listing so the dashboard can mark them `invalid`.
- `resolve_project_local_workflow(repo_path, job_type)` — used by the compile path.
- `resolve_bundled_source(name)` — used by the copy endpoint.
- `project_repo_path(root, slug)` — read `repo_path` from `project.json`.

## Copy flow (Stage 6)

`POST /api/projects/{slug}/workflows/copy`:

1. Resolve project's `repo_path` from `project.json`. 404 if missing.
2. Resolve bundled source from `hammock/templates/workflows/`. 404 if missing.
3. `dest_name = body.dest_name or f"{source}-{slug}"`.
4. `shutil.copytree(source_folder, repo_path / ".hammock" / "workflows" / dest_name)`. 409 if dest exists.
5. Run `verify_workflow_folder` against the just-copied folder. Return its result.

The operator is responsible for `git add` / `git commit`. Hammock leaves git alone.

## SSE pipeline

`api/sse.py`:

- File-watch via mtime polling.
- Emits events: `node_state_changed`, `envelope_written`, `pending_added`, `pending_removed`, `job_state_changed`.
- Frontend listens via `EventSource`, invalidates vue-query caches per event type.

Don't add expensive computation to the watch loop. Don't add new event types without coalescing where possible.

## Projections

`state/projections.py` is the only file that constructs response models. Every API endpoint either:

- Calls a `projections.<thing>(...)` function (the right way), or
- Defines its own small response model + reads the on-disk files directly (acceptable for endpoints with state mutations, like `POST /api/projects`).

Don't put HTTP concerns (status codes, request parsing) in `projections.py`. It's a pure read layer.

## Runner mode

`AppSettings.runner_mode`:

- `real` — engine spawns `claude -p` via `engine.v1.artifact._default_claude_runner`.
- `fake` — engine uses `FakeStageRunner` from `dashboard/runner/`. Set via `HAMMOCK_FAKE_FIXTURES_DIR`. Used for tests that need a real driver run without claude.

## Detail docs

- `dashboard/frontend/CLAUDE.md` — the SPA layer.
- `docs/for_agents/architecture.md` — full job lifecycle including dashboard role.
- `docs/for_agents/testing.md` — `DashboardHandle` fixture, FakeEngine vs real.
- `docs/for_agents/gotchas.md` — atomic-write rule, no caching, SSE polling cost.
