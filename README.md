# Hammock

> Agentic development harness — Claude Code AS the orchestrator.

Hammock orchestrates safe, observable, human-gated edits to source repositories. The orchestrator is a `claude -p` subprocess that walks a workflow DAG and spawns one `Task` subagent per node. The Python side persists state files, serves the dashboard, and forwards events.

## Quickstart

### Prerequisites

- macOS or Linux, Python ≥ 3.12, `git` ≥ 2.40, [`gh`](https://cli.github.com/) ≥ 2.40, [`uv`](https://docs.astral.sh/uv/), Node ≥ 20 + `pnpm`.

### Install

```bash
git clone https://github.com/NitinJ/hammock.git
cd hammock
uv sync --dev
cd dashboard/frontend && pnpm install && pnpm build && cd ../..
```

### Run

```bash
~/workspace/scripts/run-hammock-smoke.sh start
```

Dashboard at <http://127.0.0.1:8765>.

To pin a project:

```bash
HAMMOCK_PROJECT_REPO_PATH=/path/to/repo \
  ~/workspace/scripts/run-hammock-smoke.sh start
```

### Submit a job

Through the dashboard: click **+ New job**, pick a workflow, type a request, submit. The orchestrator runs against the registered project repo, opens PRs via `gh`, and pauses at HIL gates for your review.

## Architecture

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

- **Frontend** — `dashboard/frontend/`. Vue 3 SPA, vue-query, SSE for live updates.
- **Dashboard** — `dashboard/`. FastAPI. No DB; reads `~/.hammock-v2/` via projections.
- **Engine** — `hammock/engine/`. Workflow schema, runner, paths. The orchestrator prompt at `hammock/prompts/orchestrator.md` is the runtime.
- **Bundled workflows + prompts** — `hammock/{workflows,prompts}/`.

## Workflow YAML

```yaml
name: my-workflow
description: |
  Whatever this does.

nodes:
  - id: step-one
    prompt: my-prompt-template

  - id: step-two
    prompt: another-prompt
    after: [step-one]

  - id: human-gate
    prompt: review
    after: [step-two]
    human_review: true

  - id: expander
    prompt: my-expander
    kind: workflow_expander    # subagent emits expansion.yaml at runtime
    after: [human-gate]

  - id: finale
    prompt: write-summary
    after: [expander]
```

Resolution order: `<repo>/.hammock-v2/workflows/<name>.yaml` (project-local) > `~/.hammock-v2/workflows/<name>.yaml` (custom) > bundled.

## Tests

```bash
.venv/bin/python -m pytest dashboard/tests hammock/tests --tb=short
```

Default uses the fake claude runner; no tokens spent.

## See also

- `docs/hammock-v2-design.md` — design doc.
- `docs/hammock-v2-extras.md` — workflow editor + project + prompt management.
- `docs/hammock-v2-projects-and-chat.md` — project registry + 2-way operator chat.
- `docs/hammock-v2-workflow-expander.md` — runtime fan-out via `kind: workflow_expander`.
