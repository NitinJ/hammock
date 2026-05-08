# Hammock v2

Claude Code AS the orchestrator. v1 ran a Python engine that dispatched per-stage `claude -p` invocations through dispatchers + validators + iter_path keying. v2 collapses all of that into one prompt: the **orchestrator** is a Claude Code agent that walks a workflow and spawns one `Task` subagent per node.

> **Status**: v2 is parallel to v1. The `hammock_v2/` and `dashboard_v2/` packages are siblings of v1's `hammock/` and `dashboard/` and don't touch any v1 code paths. Both can run side by side (v1 on port 8765, v2 on 8766). Read the design doc at `docs/hammock-v2-design.md`.

## What you get

- **A 200-line orchestrator prompt** at `hammock_v2/prompts/orchestrator.md` — the entire engine runtime is here, in markdown.
- **A simple workflow YAML** schema (id, prompt, after, human_review). One bundled workflow: `fix-bug`.
- **Per-node markdown on disk**: `nodes/<id>/{input.md, prompt.md, output.md, state.md, chat.jsonl}`.
- **Modernized dashboard** at `dashboard_v2/`: dark theme, glassmorphism, live SSE updates, node timeline + tabbed detail (Output / Chat / Prompt / Input).

## Quickstart

### 1. Build the frontend

```
cd dashboard_v2/frontend
pnpm install
pnpm build
```

### 2. Start the dashboard

```
~/workspace/scripts/run-hammock-v2-smoke.sh start
```

The dashboard listens on port **8766**. Visit <http://127.0.0.1:8766>.

To use a specific project repo (the agent runs commits + PRs against it):

```
HAMMOCK_V2_PROJECT_REPO_PATH=/path/to/your/repo \
  ~/workspace/scripts/run-hammock-v2-smoke.sh start
```

Default: `/home/nitin/workspace/highlighter-extension`.

### 3. Submit a job

Through the dashboard: click **+ New job**, pick `fix-bug`, type a request, submit. Or via CLI:

```
~/workspace/scripts/run-hammock-v2-smoke.sh submit "There's a bug in foo. Fix it."
```

### 4. Tail the orchestrator (for debugging)

```
~/workspace/scripts/run-hammock-v2-smoke.sh tail <slug>
```

## How it works

```
Submit
  ↓
dashboard_v2/api/jobs.py:POST /api/jobs
  ↓ spawns
dashboard_v2/runner/run_job.py
  ↓ calls
hammock_v2.engine.runner.run_job
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

The schema is **5 keys total** at the node level: `id`, `prompt`, `after`, `human_review`, `description`. No types, no envelopes, no loops, no conditionals. The orchestrator decides everything else at runtime.

## Adding a new node prompt

1. Drop a `<name>.md` file into `hammock_v2/prompts/`.
2. Reference it in your workflow YAML by `prompt: <name>`.
3. The orchestrator appends a footer with `input.md`, `output.md`, and cwd instructions automatically — your prompt body is just the task description.

The bundled prompts (`write-bug-report.md`, `write-design-spec.md`, `review.md`, `write-impl-spec.md`, `implement.md`, `pr-create.md`, `write-summary.md`) are good references for the imperative-phasing pattern.

## Human review

If a node has `human_review: true`:

1. The orchestrator spawns the subagent normally.
2. After `output.md` is written, the orchestrator writes `awaiting_human.md`.
3. The dashboard surfaces a "Awaiting your review" panel on that node.
4. Operator clicks **Approve** or **Needs revision** (with comment).
5. The dashboard POSTs to `/api/jobs/{slug}/nodes/{id}/human_decision` which writes `human_decision.md`.
6. The orchestrator polls for that file, reads it, and continues. On `needs-revision`, it re-spawns the subagent with the human's comment as added context, up to 3 cycles.

## Caveats

- **Frontend is built once at install, not at runtime.** If you change `dashboard_v2/frontend/src/`, run `pnpm build` again before refreshing.
- **Real-claude flakes are now visible.** If a subagent doesn't write `output.md`, the orchestrator retries once and then fails the job. v0/v1 used to mask these by reading stale envelopes — v2 surfaces them.
- **No loops in the workflow schema.** If you need iteration, write multiple nodes or have the orchestrator decide based on prior outputs (the prompt allows it; it's just not declarative).
- **Deletion is your job.** Old jobs accumulate under `~/.hammock-v2/jobs/`. Wipe with `rm -rf ~/.hammock-v2/jobs/<slug>` when you're done.

## Tests

```
.venv/bin/python -m pytest hammock_v2/tests dashboard_v2/tests --tb=short
```

19 + 13 = 32 tests. Backend uses a fake claude runner; no real tokens spent.

## What's NOT in v2 today

- Multiple workflows. Only `fix-bug` ships. Add more under `hammock_v2/workflows/`.
- Project management UI. Single project (set via env var).
- SSE-driven incremental UI updates. The dashboard polls every 2-5 seconds via vue-query; that's enough for a small operator workflow but burns more requests than SSE would.
- Retry-on-failure UI affordance. Failed jobs stay failed; you'd re-submit.

## See also

- `docs/hammock-v2-design.md` — the design doc.
- `docs/hammock-workflow.md` — v1 workflow customization design (still relevant for the prompt-template pattern).
