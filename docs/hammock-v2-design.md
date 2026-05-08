# Hammock v2 — design

Status: design — implementation in flight.

A complete overhaul. Replaces the Python-orchestrated engine + per-stage claude invocations with **Claude Code AS the orchestrator**. The Python side is now a thin shell: it spawns one master claude agent per job, watches the on-disk job dir, and serves the dashboard.

## What changes

| Layer | v1 (today) | v2 |
|---|---|---|
| **Engine** | Python: `engine/v1/driver.py`, dispatchers per node-kind, validators, predicate evaluator, loop semantics, $ref pointers, iter_path keying | A single Claude Code agent reads `workflow.yaml` and spawns subagent Tasks. Python only spawns the orchestrator. |
| **Node kinds** | artifact / code / loop × agent / human / engine | One kind: **agent**. Period. |
| **Workflow spec** | Pydantic schema with discriminated unions, `after:`, `runs_if:`, `until:`, `count:`, `outputs:` projections, etc. | Plain list of nodes. `id`, `prompt`, optional `after:`, optional `human_review: true`. That's it. |
| **State on disk** | typed envelopes (`<var>__<token>.json`), pending markers, run dirs, projection $refs, nested loop iter tokens | One folder per node: `nodes/<id>/{input.md, prompt.md, output.md, state.md, chat.jsonl}`. All markdown. |
| **HIL** | dashboard form, pending marker, type-driven form schema | A node with `human_review: true`. The orchestrator pauses; human writes `nodes/<id>/output.md` directly (or via dashboard textarea). Orchestrator resumes. |
| **PR creation** | engine calls `gh pr create` from substrate code | A `pr-create` agent node that runs the gh CLI. Engine just dispatches it. |
| **Loops** | first-class, with substrate semantics | dropped from v2. If you need iteration, write multiple nodes or have the orchestrator decide. KISS. |
| **Code worktrees** | `repo-worktrees/<id>/` substrate manager | Agent runs in `<job_dir>/repo`. Branch management is the agent's job (it's in its prompt). |
| **Dashboard** | Vue 3 + Tailwind, functional but utilitarian | Same stack, modernized — dark theme, glassmorphism, AI-startup professional aesthetic. |

The fundamental shift: **Claude Code's Task tool replaces Python's dispatcher**. Loops, conditionals, retries — all become things the orchestrator agent decides, not engine code.

## On-disk layout

```
~/.hammock-v2/jobs/<slug>/
├── job.md                  # Job metadata: state, request, started, finished
├── workflow.yaml           # Snapshot of the workflow being run
├── orchestrator.jsonl      # Engine claude's stream-json output (live-tailable)
├── orchestrator.log        # Plain-text log from the runner shim
├── repo/                   # Project clone (if workflow needs it — agent decides)
└── nodes/
    └── <node_id>/
        ├── input.md        # What this node received (resolved inputs as MD)
        ├── prompt.md       # Rendered prompt the agent saw
        ├── output.md       # The agent's output narrative + any structured fields
        ├── state.md        # YAML frontmatter: status, started_at, finished_at, error
        └── chat.jsonl      # Agent's stream-json output
```

Everything is a markdown file. No JSON envelopes. No type registry. The dashboard renders `output.md` directly with a markdown pipeline. The orchestrator passes prior nodes' `output.md` content as context to subsequent nodes via inputs.

## Workflow specification

```yaml
# workflows/fix-bug.yaml
name: fix-bug
description: |
  Standard bug-fix workflow. Each node receives the request + all prior
  outputs as context, and writes its own output.md.

nodes:
  - id: write-bug-report
    prompt: write-bug-report

  - id: write-design-spec
    prompt: write-design-spec
    after: [write-bug-report]

  - id: review-design-spec
    prompt: review
    after: [write-design-spec]
    human_review: true        # orchestrator pauses; dashboard surfaces a textarea

  - id: write-impl-spec
    prompt: write-impl-spec
    after: [review-design-spec]

  - id: implement
    prompt: implement
    after: [write-impl-spec]

  - id: open-pr
    prompt: pr-create
    after: [implement]

  - id: write-summary
    prompt: write-summary
    after: [open-pr]
```

That's the entire schema. No types, no envelopes, no loop semantics. The orchestrator interprets it and spawns subagents.

## The orchestrator agent

A single prompt at `prompts/orchestrator.md`. The Python runner spawns:

```
claude --output-format stream-json --verbose -p "$(cat orchestrator.md)" \
  --permission-mode bypassPermissions
```

The orchestrator's instructions, in shape:

1. Read `workflow.yaml` from the job dir.
2. Topo-sort nodes by `after:` edges.
3. For each node:
   - Build the node's input.md by concatenating prior outputs' `output.md`.
   - Spawn a Task subagent with the node's prompt + input + a write target (`nodes/<id>/output.md`).
   - On Task return, verify `output.md` exists; if not, retry once.
   - If `human_review: true`: write a marker, wait until `output.md` exists.
   - Update `nodes/<id>/state.md`.
4. When all done, mark `job.md` complete.

The orchestrator is itself a Claude agent — it gets to use Read/Write/Bash/Task tools. Loops, retries, parallelism (when nodes have no after-edge between them) are all decisions the orchestrator makes from its prompt, not engine code.

## Dashboard

**Stack unchanged**: FastAPI + Vue 3 + Tailwind. **Aesthetic**: complete overhaul.

- Dark, professional. Single accent color (electric blue or violet). Subtle glassmorphism on panels.
- Typography: Inter for UI, JetBrains Mono for code/state.
- Job list as cards, not table rows. Each card shows the workflow's progress as a small node-graph.
- Job detail page: left pane — vertical node timeline with state pills. Right pane — selected node's `output.md` rendered as markdown, with chat tail in a collapsible drawer below.
- Live updates via SSE (file mtime watcher).
- Submit screen: simple request textarea + workflow picker. Modern submit interaction.
- Settings + projects screens kept lightweight.

## Python surface

Drastically reduced.

```
hammock-v2/
├── engine/v2/
│   ├── runner.py          # Spawns orchestrator claude, tails its output
│   └── workflow.py        # Loader for the simple YAML schema
├── prompts/
│   ├── orchestrator.md
│   ├── write-bug-report.md
│   ├── write-design-spec.md
│   ├── review.md
│   ├── write-impl-spec.md
│   ├── implement.md
│   ├── pr-create.md
│   └── write-summary.md
├── workflows/
│   └── fix-bug.yaml
└── dashboard/
    ├── api/               # /api/jobs, /api/jobs/{slug}, /api/jobs/{slug}/nodes/{id}, /sse/job/{slug}, hil submission
    └── frontend/          # the modernized SPA
```

No `engine/v1/`, no `shared/v1/types/`, no `shared/v1/envelope.py`, no validator, no predicate evaluator, no loop_dispatch. All of those concepts move into the orchestrator's prompt.

## What we keep from v1

- The dashboard's FastAPI shell + frontend stack.
- The `claude --output-format stream-json` invocation pattern.
- The `chat.jsonl` per-attempt convention (orchestrator + each subagent get their own).
- Memory of what tripped us up (the `gotchas.md` lessons that v1 paid for).

## What we lose

- Loops as a workflow primitive (orchestrator handles iteration if needed).
- Strict typed envelopes (markdown is the wire format).
- Static workflow validation (orchestrator decides what's valid at run time).
- Multi-attempt-per-node history (single attempt; if it fails the orchestrator decides).

These are intentional trades. The orchestrator-as-agent pattern is more flexible at runtime; the Python schema rigidity that v1 paid for is the cost.

## Migration

`hammock-v2/` lives alongside `hammock/` (the v1 codebase) on the `hammockv2` branch. Both work, both have their own dashboard. After validation, `hammock/` is deleted. Don't try to make v1 jobs runnable under v2 — incompatible by design.

## E2E target

A real fix-bug job submitted from the modernized dashboard, against the highlighter-extension project, runs end to end:
- Orchestrator agent spawns, reads workflow, walks the DAG.
- Each subagent does its work, writes `output.md`.
- Human review node pauses, accepts approval from dashboard.
- Implementation node creates a branch, edits files, commits.
- PR-create node opens the PR via `gh`.
- Summary node writes the wrap-up.
- Job marked complete.

Real claude. Real GitHub. End to end.
