# Hammock

Claude Code AS the orchestrator. The **orchestrator** is a Claude Code agent that walks a workflow and spawns one `Task` subagent per node. The Python side is a thin substrate: it persists files, serves the dashboard, and forwards events.

## What you get

- **A small orchestrator prompt** at `hammock/prompts/orchestrator.md` — the engine runtime is here, in markdown.
- **A simple workflow YAML** schema (id, prompt, after, human_review, kind, requires). Bundled workflows include `fix-bug` and `stage-implementation`.
- **Per-node markdown on disk**: `nodes/<id>/{input.md, prompt.md, output.md, state.md, chat.jsonl}`.
- **Dashboard** at `dashboard/`: dark theme, live SSE updates, node timeline, project + prompt management, workflow editor.

## Quickstart

### 1. Build the frontend

```
cd dashboard/frontend
pnpm install
pnpm build
```

### 2. Start the dashboard

```
~/workspace/scripts/run-hammock-smoke.sh start
```

The dashboard listens on port **8765**. Visit <http://127.0.0.1:8765>.

To use a specific project repo (the agent runs commits + PRs against it):

```
HAMMOCK_PROJECT_REPO_PATH=/path/to/your/repo \
  ~/workspace/scripts/run-hammock-smoke.sh start
```

### 3. Submit a job

Through the dashboard: click **+ New job**, pick a workflow, type a request, submit.

## How it works

```
Submit
  ↓
dashboard/api/jobs.py:POST /api/jobs
  ↓ spawns
dashboard/runner/run_job.py
  ↓ calls
hammock.engine.runner.run_job
  ↓ spawns
claude -p <orchestrator prompt> --output-format stream-json --verbose
  │
  │ The orchestrator agent then:
  │   1. Reads workflow.yaml
  │   2. Topo-sorts the DAG
  │   3. For each node:
  │      - writes input.md (request + prior outputs)
  │      - writes prompt.md (template + footer telling subagent where to write)
  │      - state.md → running
  │      - spawns Task subagent (claude code's built-in tool)
  │      - waits for output.md to materialize
  │      - state.md → succeeded
  │   4. If human_review: write awaiting_human.md, poll for human_decision.md
  ↓
Job dir at ~/.hammock-v2/jobs/<slug>/
```

> **Storage path**: the on-disk root is still `~/.hammock-v2/` to preserve continuity with existing jobs. The package layout no longer has a `_v2` suffix.

## Files on disk

```
~/.hammock-v2/jobs/<slug>/
├── job.md                  # YAML frontmatter: state, request, timestamps
├── workflow.yaml           # snapshot
├── orchestrator.jsonl      # the master claude's stream-json (live-tailable)
├── orchestrator.log        # plain-text stderr
├── repo/                   # project clone (if configured)
└── nodes/<id>/
    ├── input.md            # orchestrator-written context
    ├── prompt.md           # rendered template + footer
    ├── output.md           # subagent's narrative (the source of truth)
    ├── state.md            # YAML frontmatter: state, timestamps
    ├── chat.jsonl          # subagent's stream-json
    ├── awaiting_human.md   # written by orchestrator when paused
    └── human_decision.md   # written by dashboard when human responds
```

Everything is markdown. The dashboard renders `output.md` directly with a sanitized markdown pipeline.

## Workflow specification

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

  - id: finale
    prompt: write-summary
    after: [human-gate]
```

The schema is small: `id`, `prompt`, `after`, `human_review`, `kind`, `requires`, `description`. The orchestrator decides everything else at runtime.

## Adding a new node prompt

1. Drop a `<name>.md` file into `hammock/prompts/`.
2. Reference it in your workflow YAML by `prompt: <name>`.
3. The orchestrator appends a footer with `input.md`, `output.md`, and cwd instructions automatically — your prompt body is just the task description.

The bundled prompts are good references for the imperative-phasing pattern.

## Human review

If a node has `human_review: true`:

1. The orchestrator spawns the subagent normally.
2. After `output.md` is written, the orchestrator writes `awaiting_human.md`.
3. The dashboard surfaces a "Awaiting your review" panel on that node.
4. Operator clicks **Approve** or **Needs revision** (with comment).
5. The dashboard POSTs to `/api/jobs/{slug}/nodes/{id}/human_decision` which writes `human_decision.md`.
6. The orchestrator polls for that file, reads it, and continues. On `needs-revision`, it re-spawns the subagent with the human's comment as added context, up to 3 cycles.

## Caveats

- **Frontend is built once at install, not at runtime.** If you change `dashboard/frontend/src/`, run `pnpm build` again before refreshing.
- **Real-claude flakes are visible.** If a subagent doesn't write `output.md`, the orchestrator retries once and then fails the job.
- **No loops in the workflow schema.** Use a `workflow_expander` node if you need runtime fan-out.
- **Deletion is your job.** Old jobs accumulate under `~/.hammock-v2/jobs/`. Wipe with `rm -rf ~/.hammock-v2/jobs/<slug>` when you're done.

## Tests

```
.venv/bin/python -m pytest hammock/tests dashboard/tests --tb=short
```

Backend uses a fake claude runner by default; no real tokens spent.

## See also

- `docs/hammock-v2-design.md` — the design doc.
- `docs/hammock-v2-workflow-expander.md` — runtime fan-out via `kind: workflow_expander`.
