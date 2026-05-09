# Hammock

Hammock is a workflow runner where Claude Code IS the orchestrator. A `claude -p` subprocess walks a workflow DAG, spawns one `Task` subagent per node, and persists per-node markdown on disk. Human-in-the-loop gates are first-class.

## How it works

```
┌──────────────────┐    HTTP/SSE    ┌──────────────────┐    spawns      ┌──────────────────┐
│  Vue 3 frontend  │ ─────────────> │  FastAPI dash    │ ─────────────> │  claude -p       │
│  (vue-query)     │ <───────────── │   + projections  │                │  (orchestrator)  │
└──────────────────┘                └──────────────────┘                └─────────┬────────┘
                                              │ reads/writes                     │ Task() per node
                                              ▼                                  ▼
                                   ~/.hammock-v2/jobs/<slug>/                <subagent>
                                   (per-node markdown,
                                    SSE event log,
                                    human-decision markers)
```

The orchestrator is a Claude Code agent, not a Python state machine. The Python side persists state files, serves the dashboard, and forwards events.

## Layout

- `dashboard/` — FastAPI service + Vue 3 SPA. No DB; reads job state directly from `~/.hammock-v2/`.
  - `dashboard/api/` — REST + SSE endpoints, projections.
  - `dashboard/jobs/`, `dashboard/runner/` — job lifecycle, orchestrator process spawn.
  - `dashboard/projects.py`, `dashboard/workflows.py` — registry surfaces.
  - `dashboard/frontend/` — Vue 3 SPA, vue-query, SSE-driven live updates.
  - `dashboard/tests/` — backend tests (FakeEngine + per-API contract).
- `hammock/` — engine substrate.
  - `hammock/engine/` — workflow schema, runner, paths.
  - `hammock/prompts/` — bundled prompts (orchestrator + per-node templates + helpers).
  - `hammock/workflows/` — bundled workflows.
  - `hammock/tests/` — engine + prompt tests.
- `docs/` — design docs, specs, memory.

## Running

```bash
# build the SPA + start the dashboard on :8765
~/workspace/scripts/run-hammock-smoke.sh start

# point at a specific repo
HAMMOCK_PROJECT_REPO_PATH=/path/to/repo \
  ~/workspace/scripts/run-hammock-smoke.sh start
```

## On-disk paths

- Storage root: `~/.hammock-v2/` (dir name is historical; do not rename — preserves existing job state).
- Per-project overrides: `<repo>/.hammock-v2/{workflows,prompts}/`.
- Bundled workflows + prompts ship with the wheel under `hammock/{workflows,prompts}/`.

Resolution order for a workflow or prompt name: project-local > custom (cross-project, under `~/.hammock-v2/`) > bundled.

## Tests

```bash
.venv/bin/python -m pytest dashboard/tests hammock/tests --tb=short
```

Backend uses a fake claude runner by default (`HAMMOCK_RUNNER_MODE=fake`); no real tokens spent.

## Hard rules for agents working in this repo

- Never push to `main` without operator permission. The accumulated branch is `hammockv2`.
- Run the full pre-push gauntlet (ruff format, ruff check, pyright, pytest, pnpm build) before any push.
- Don't edit generated files (`*.g.dart`, `*.freezed.dart`, `dashboard/frontend/dist/`, `dashboard/frontend/node_modules/`).
- Frontend changes need `pnpm build` before refreshing the dashboard — the dashboard serves the built SPA, not the dev server.

## See also

- `docs/hammock-v2-design.md` — the design doc.
- `docs/hammock-v2-extras.md` — workflow editor + project + prompt management.
- `docs/hammock-v2-projects-and-chat.md` — project registry + 2-way operator chat.
- `docs/hammock-v2-workflow-expander.md` — runtime fan-out via `kind: workflow_expander`.
