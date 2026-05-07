# Hammock

Hammock is a workflow runner that orchestrates Claude agents through a multi-step DAG with optional human-in-the-loop gates. It exists because real-claude one-shot runs are too brittle for non-trivial software work — a multi-stage workflow keeps the agent grounded in the codebase, validates intermediate artifacts, and lets humans intervene at the gates that matter.

## What it is

A single workflow run goes through stages like:

1. `write-bug-report` — agent reads the user's request and produces a structured bug report (typed envelope).
2. `write-design-spec` — agent reads the bug report and produces a design spec, grounded in the actual codebase.
3. `review-design-spec-agent` then `review-design-spec-human` — agent and operator review.
4. `write-impl-spec` → `write-impl-plan` — same shape.
5. `implement` (code node) — agent gets a git worktree, commits on a stage branch, engine pushes and opens a PR.
6. Operator merges the PR. Engine continues.
7. `tests-and-fix` (optional) → `write-summary` — final summary.

Workflows are declarative YAML; agents see a per-node markdown prompt; everything between agent and engine is a typed Pydantic envelope on disk.

## Why this design

- **The agent runs in the project repo, not in a sandbox.** `cwd = <job_dir>/repo` — `CLAUDE.md`, code, conventions all auto-load. The agent can `Grep` and `Read` to verify entities exist before referencing them in a design spec.
- **Every step produces a typed envelope.** Designs aren't free-text; they are validated JSON with a `document: str` markdown field. Downstream agents consume the structured fields *and* the prose.
- **Reviewers are first-class.** A workflow can declare HIL gates. The driver writes a pending marker, transitions to `BLOCKED_ON_HUMAN`, waits for the dashboard to land the answer, then continues.
- **No cloud dependency.** Hammock root is `~/.hammock/`. State is files on disk: `jobs/<slug>/`, `projects/<slug>/`. Real claude is a local subprocess. GitHub is the only external service (for code-node PRs).

## Vision

Operators customize Hammock per-project by editing workflow yamls + prompt `.md` files in their own repo under `<repo>/.hammock/workflows/<name>/`. The dashboard surfaces these alongside bundled workflows; the engine prefers project-local on resolve. One-click "Copy to project" forks a bundled workflow into the project for editing.

## High-level architecture

```
┌──────────────────┐    HTTP/SSE    ┌──────────────────┐    spawn     ┌──────────┐
│  Vue 3 frontend  │ ─────────────> │  FastAPI dash    │ ───────────> │  Engine  │
│  (Vite + Pinia)  │ <───────────── │   + projections  │              │  driver  │
└──────────────────┘                └──────────────────┘              └─────┬────┘
                                              │ writes                      │ spawns
                                              ▼                             ▼
                                   ~/.hammock/jobs/<slug>/         claude -p / git / gh
                                   (typed envelopes,
                                    pending markers,
                                    workflow.yaml snapshot)
```

- **Frontend** — `dashboard/frontend/`. Vue 3 SPA. All API via `api/queries.ts` (vue-query). SSE for live updates.
- **Dashboard** — `dashboard/`. FastAPI. No DB; reads `~/.hammock/` directly via projections in `dashboard/state/`. Spawns one engine driver subprocess per submitted job.
- **Engine** — `engine/v1/`. Driver topologically walks the workflow DAG, dispatches each node by kind (artifact / code / loop), persists state per node.
- **Shared contracts** — `shared/v1/`. Pydantic models for `Workflow`, `Envelope`, `JobConfig`, `NodeRun`. Variable type registry. Path layout helpers.
- **Bundled workflows** — `hammock/templates/workflows/<name>/{workflow.yaml, prompts/}`.

## How to run locally

```
scripts/run-hammock.sh             # build SPA, run dashboard (real claude)
scripts/run-hammock.sh --dev       # vite dev (5173) + dashboard (8765)
HAMMOCK_FAKE_FIXTURES_DIR=...      # use FakeStageRunner instead of real claude
```

Dashboard at http://localhost:8765. Submit a job via UI or:

```
curl -X POST http://localhost:8765/api/jobs -d '{"project_slug": "...", "job_type": "fix-bug", ...}'
```

## Index of agent docs

These live under `docs/for_agents/` and are the source of truth for agents working in this repo:

- [architecture.md](docs/for_agents/architecture.md) — Job lifecycle, substrate model, variable resolution, HIL flow, SSE pipeline.
- [development-process.md](docs/for_agents/development-process.md) — TDD red→green→refactor; one stage = one PR; preflight gauntlet; CI gates.
- [testing.md](docs/for_agents/testing.md) — Layered testing strategy: which layer to add a test at, FakeEngine vs real claude, file layout.
- [rules.md](docs/for_agents/rules.md) — Hard rules for agents and humans: never push without PR, full preflight before push, document field on narrative types, etc.
- [gotchas.md](docs/for_agents/gotchas.md) — Concrete footguns observed in this codebase: lint-after-format ordering, pyright strict, real-claude prompt tuning, HIL race fixed, empty-stdout failure mode.
- [memory.md](docs/for_agents/memory.md) — Per-stage learnings from the workflow customization plan, design decisions made along the way.

## Per-component CLAUDE.md

Drill down to the component you're touching:

- `engine/v1/CLAUDE.md` — workflow execution: driver, dispatchers, prompt assembly, substrate.
- `dashboard/CLAUDE.md` — FastAPI service, projections, compile path.
- `dashboard/frontend/CLAUDE.md` — Vue 3 SPA, vue-query, SSE.
- `shared/v1/CLAUDE.md` — Pydantic contracts shared by engine + dashboard.
- `tests/CLAUDE.md` — test layout, FakeEngine, when to write at which layer.
- `hammock/templates/workflows/CLAUDE.md` — bundled workflow folder shape.

## When in doubt

`docs/for_agents/rules.md` is the short list. `docs/for_agents/gotchas.md` is the "I wish I'd known" list. Read both before opening a PR.
