# Hammock Implementation Plan

**Date:** 2026-05-02
**Status:** v0 implementation plan complete and dispatch-ready.
**Companion to:** `2026-05-01-hammock-design.md` (the design doc; canonical for *what*; this doc is canonical for *how* and *in what order*).
**Path:** `~/workspace/hammock/`

---

## 1. How to read this — [Complete]

Two audiences. Read in either mode.

**For the orchestrator (the human):** § Stage map is the DAG. Sections under § Stages are PR-shaped units of work. Pick a ready stage, dispatch an agent, review the PR, merge. Repeat. Stages with no dependency between them can run in parallel — § Stage map calls out which.

**For an agent executing a stage:** the stage section is self-contained. It names its goal, what stages must be merged before you start, the file paths you may touch, the task DAG (test / impl / verify, mirroring the design doc's stage shape), and the acceptance criteria. The design doc remains the source of truth for *what*; this doc tells you *which slice of it to build now*. When in doubt about behaviour, defer to the design doc and surface the ambiguity via `mcp__dashboard__open_ask` — except the dashboard doesn't exist yet, so for early stages: post the question in the PR description and tag the orchestrator.

**Meta-recursion note.** Until Hammock is self-hosting (Stage 16), the human orchestrates stages by hand. This doc is the manual-stage-list-yaml. Once Stages 0–15 land, the orchestrator can start a Hammock-on-Hammock instance and let it run the rest.

---

## 2. Implementation philosophy — [Complete]

Six principles. Each is load-bearing; deviations are red flags.

1. **Storage-first.** Pydantic models, path canonicalisation, and atomic write helpers ship before any process logic. Everything else imports them. The single-writer-per-file discipline is enforced in code structure, not in code reviews.

2. **Stub before integrate.** Stages that talk to a real Claude Code subprocess (5, 6, 15, 16) come after stages that can be tested with stubs (0–4, 7–10). When an upstream dep doesn't exist yet, build the stage against a fake that satisfies the interface, then swap.

3. **Each stage produces one PR.** PRs above ~600 LOC of net-new code (excluding generated types, lockfiles, tests) split into sub-PRs. The stage isn't done until the PR is merged.

4. **Each merged PR leaves the system runnable.** Even if partial, what's merged works end-to-end for the slice it covers. No "this only works once Stage X+1 lands."

5. **Test/Impl/Verify per stage.** Mirrors the design doc's task quartet. Every stage's task DAG includes a verify task that's not the same as "tests pass" — it's a behavioural demonstration of the new capability.

6. **Bounded by interfaces, not ambition.** A stage's *Files changed* list is its hard scope. Tempting cross-cutting cleanups go to a separate stage, even if obvious — keeping merges reviewable matters more than minimising commits.

---

## 3. Tech stack — finalised — [Complete]

The design doc's § Presentation plane § Stack locked the high-level choices. This section pins exact libraries and versions for v0.

### Backend (Python)

| Concern | Choice | Version | Notes |
|---|---|---|---|
| Language | Python | 3.12+ | Pin in `pyproject.toml`; CI runs 3.12 and 3.13. |
| Package manager | uv | ≥0.4 | Locked. `uv.lock` checked in. |
| Web framework | FastAPI | ≥0.110 | Uses Pydantic v2 natively. |
| Models | Pydantic | v2.6+ | Strict mode by default. |
| ASGI server | uvicorn | ≥0.27 | Single worker (locked in design doc). |
| File watching | watchfiles | ≥0.21 | Rust-backed; cross-platform. |
| CLI | typer | ≥0.12 | Built on Click; type-hinted; pairs with Pydantic models. |
| Terminal output | rich | ≥13 | Used for `hammock doctor`, status views. |
| Subprocess streaming | stdlib `asyncio.subprocess` | — | No third-party process manager. |
| HTTP client (gh polling) | httpx | ≥0.27 | async; used for any outbound HTTP. |
| MCP server | mcp (Python SDK) | latest stable | Stdio transport; used per-stage. |
| Test framework | pytest + pytest-asyncio | ≥8 / ≥0.23 | |
| Test fixtures | pytest-fixtures, factory-boy | latest | factory-boy for Pydantic model factories. |
| Property tests | hypothesis | ≥6 | For path canonicalisation, slug derivation, predicate evaluator. |
| Coverage | coverage + pytest-cov | latest | Target: 85%+ on shared/, 75%+ overall. |
| Type checker | pyright | ≥1.1.350 | Strict mode on `shared/` and `dashboard/`; basic on tests. |
| Linter / formatter | ruff | ≥0.4 | Replaces black + flake8 + isort. |

### Frontend (TypeScript)

| Concern | Choice | Version | Notes |
|---|---|---|---|
| Language | TypeScript | 5.4+ | Strict mode; `noUncheckedIndexedAccess: true`. |
| Package manager | pnpm | ≥9 | Faster than npm; better workspace support if we add one. |
| Framework | Vue | 3.4+ | Composition API only; `<script setup>` SFCs. |
| Build tool | Vite | 5+ | Dev server + prod build. |
| State | Pinia | 2.1+ | Replaces Vuex; one store per cache scope. |
| Router | Vue Router | 4.3+ | Lazy routes for code-splitting. |
| CSS | Tailwind CSS | 3.4+ | Plus `@tailwindcss/typography` for `<MarkdownView>`. |
| Data fetching | TanStack Query (Vue) | 5+ | Snapshot path; SSE patches the cache. |
| SSE | Native `EventSource` | — | No library. |
| Schema sync | openapi-typescript | ≥7 | FastAPI's `/openapi.json` → `src/api/schema.d.ts`. Run via npm script + pre-commit. |
| Markdown | unified + remark-* | latest | `remark-gfm`, `remark-html`, `rehype-highlight`. |
| Charts | ECharts + vue-echarts | ≥5 / ≥6 | Cost dashboard; tree-shakeable. |
| Virtualisation | vue-virtual-scroller | ≥2 | Agent0 stream pane. |
| Composables | @vueuse/core | ≥10 | `useEventSource`, `useScroll`, etc. |
| Test runner | vitest + @vue/test-utils | latest | Unit + component. |
| E2E | Playwright | ≥1.43 | One smoke spec; expand in Stage 16. |
| Linter | ESLint + eslint-plugin-vue + @typescript-eslint | latest | Flat config. |
| Formatter | Prettier | latest | Shared config; runs in pre-commit. |

### Tooling and CI

| Concern | Choice | Notes |
|---|---|---|
| Git hooks | pre-commit | Runs ruff, prettier, schema sync. |
| CI | GitHub Actions | Three workflows: backend (lint+type+test), frontend (lint+type+test), e2e (Playwright on PRs touching either). |
| Commit style | Conventional Commits | `feat(stage-N): ...`. |
| Branching | GitHub-flow | `feat/stage-NN-<short-name>` off `main`; squash-merge. |
| Versioning | Stage-tagged | Tag `v0.<stage-num>` on each merged stage; `v0.16` is v0 release. |

### Decisions captured here

- **uv over poetry/pip.** Faster; lockfiles deterministic; aligns with Astral toolchain (ruff is also Astral).
- **pnpm over npm/yarn.** Faster installs; better disk usage. No workspace at v0; pnpm chosen for future-proofing.
- **Pyright over mypy.** Faster; better Pydantic v2 inference; better Vue/TS-side parity (LSP).
- **Single uvicorn worker.** Locked in design doc § Process structure.
- **No SSR, no Next/Nuxt-equivalent.** Locked.
- **No Redis, no Postgres for v0.** Locked.
- **No GitHub MCP, no webhooks.** Locked. `gh` CLI subprocess only.
- **No Playwright for early stages.** Stage 16 only — frontend smoke. Earlier stages rely on vitest + component tests.

---

## 4. Repository structure — [Complete]

Final layout. Each stage modifies a known subset.

```
hammock/
├── pyproject.toml
├── uv.lock
├── .python-version                   # 3.12
├── README.md
├── .gitignore
├── .pre-commit-config.yaml
├── .github/
│   └── workflows/
│       ├── backend.yml
│       ├── frontend.yml
│       └── e2e.yml
│
├── shared/                           # imported by dashboard AND job_driver
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── project.py                # Project, ProjectConfig
│   │   ├── job.py                    # JobState, JobConfig, JobCostSummary
│   │   ├── stage.py                  # StageDefinition, StageRun, StageState
│   │   ├── task.py                   # TaskState, TaskRecord
│   │   ├── hil.py                    # HilItem, AskQ/A, ReviewQ/A, ManualStepQ/A
│   │   ├── events.py                 # Event envelope + per-type payloads
│   │   ├── plan.py                   # plan.yaml shape
│   │   ├── presentation.py           # PresentationBlock, UiTemplate
│   │   ├── specialist.py             # AgentDef, SkillDef, SpecialistCatalogue
│   │   └── verdict.py                # ReviewVerdict (canonical)
│   ├── paths.py                      # canonical hammock_root layout
│   ├── atomic.py                     # atomic_write, atomic_replace
│   ├── slug.py                       # slug derivation, validation
│   └── predicate.py                  # plan.yaml predicate grammar evaluator
│
├── dashboard/                        # the long-lived dashboard process
│   ├── __init__.py
│   ├── __main__.py                   # entry: uvicorn.run(app, ...)
│   ├── app.py                        # FastAPI + lifespan
│   ├── settings.py                   # env-driven config
│   │
│   ├── api/
│   │   ├── __init__.py               # router aggregation
│   │   ├── projects.py
│   │   ├── jobs.py
│   │   ├── stages.py
│   │   ├── hil.py
│   │   ├── chat.py
│   │   ├── artifacts.py
│   │   ├── costs.py
│   │   ├── observatory.py            # v0 stub
│   │   └── sse.py
│   │
│   ├── state/
│   │   ├── __init__.py
│   │   ├── cache.py                  # in-memory typed cache
│   │   ├── projections.py            # cache → view shapes
│   │   └── pubsub.py                 # in-process scoped pub/sub
│   │
│   ├── watcher/
│   │   ├── __init__.py
│   │   └── tailer.py                 # watchfiles → cache.apply → pubsub
│   │
│   ├── hil/
│   │   ├── __init__.py
│   │   ├── state_machine.py
│   │   ├── contract.py               # get_open_items, submit_answer
│   │   └── orphan_sweeper.py
│   │
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── server.py                 # tools: open_task, update_task, open_ask, append_stages
│   │   ├── manager.py                # spawn/dispose per active stage
│   │   └── channel.py                # --channels push (writes nudges.jsonl)
│   │
│   ├── driver/
│   │   ├── __init__.py
│   │   ├── supervisor.py             # heartbeat checks, restart policy
│   │   ├── lifecycle.py              # spawn driver on job submit
│   │   └── ipc.py                    # SIGTERM + command-file writes
│   │
│   ├── compiler/
│   │   ├── __init__.py
│   │   ├── compile.py                # template loader, override merger, validator
│   │   ├── overrides.py              # modify-only deep merge
│   │   └── validators.py             # plan.yaml validation rules
│   │
│   └── frontend/
│       ├── package.json
│       ├── pnpm-lock.yaml
│       ├── vite.config.ts
│       ├── tsconfig.json
│       ├── tailwind.config.ts
│       ├── postcss.config.js
│       ├── index.html
│       ├── src/
│       │   ├── main.ts
│       │   ├── App.vue
│       │   ├── router.ts
│       │   ├── api/                  # generated types + thin fetch wrappers
│       │   │   ├── schema.d.ts       # generated by openapi-typescript
│       │   │   ├── client.ts
│       │   │   └── queries.ts        # TanStack Query hooks
│       │   ├── stores/               # Pinia stores
│       │   ├── sse.ts                # EventSource wrapper w/ replay
│       │   ├── components/
│       │   │   ├── stage/            # Agent0StreamPane, SubAgentRegion, ToolCall, ChatInput, ...
│       │   │   ├── forms/            # FormRenderer, AskForm, ReviewForm, ManualStepForm
│       │   │   ├── shared/           # StateBadge, CostBar, ConfirmDestructive, MarkdownView, ...
│       │   │   └── nav/
│       │   └── views/                # one component per route
│       ├── tests/
│       │   ├── unit/
│       │   └── e2e/                  # Playwright
│       └── dist/                     # vite build output (served as static)
│
├── job_driver/                       # separate OS process per active job
│   ├── __init__.py
│   ├── __main__.py                   # entry: python -m job_driver <job_slug>
│   ├── runner.py                     # state-machine loop
│   ├── stage_runner.py               # spawn CLI session per stage
│   └── stream_extractor.py           # parse stream-json → messages/tool-uses jsonl
│
├── cli/
│   ├── __init__.py
│   ├── __main__.py                   # entry: hammock <subcommand>
│   ├── project.py                    # register, list, info, rename, deregister, doctor
│   ├── job.py                        # submit, list, cancel
│   └── doctor.py                     # cross-project doctor
│
├── tests/
│   ├── conftest.py                   # shared fixtures
│   ├── shared/                       # tests for shared/
│   ├── dashboard/                    # tests for dashboard/
│   ├── job_driver/
│   ├── cli/
│   ├── e2e/                          # cross-process integration
│   └── fixtures/
│       └── toy-repo/                 # tiny test project for E2E
│
├── docs/
│   ├── design.md                     # the design doc, copied here
│   ├── implementation.md             # this doc
│   └── runbook.md                    # ops reference (post-Stage 16)
│
└── ~/.hammock/                       # NOT in repo; created at first run
    ├── agents/                       # global agent files (hammock-shipped)
    ├── skills/                       # global skill dirs (hammock-shipped)
    ├── ui-templates/                 # global v0 templates (8)
    ├── hooks/                        # global hook scripts
    ├── projects/<slug>/              # per-project metadata
    ├── jobs/<job_slug>/              # active and archived jobs
    └── observatory/                  # archives + metrics
```

Three layout invariants worth being explicit about:

- **`shared/` is the contract surface.** Both `dashboard/` and `job_driver/` import it. Nothing in `shared/` imports from `dashboard/` or `job_driver/`.
- **`hil/` does not import from `api/`.** Domain doesn't know transport. Verified in CI by an import-linter rule.
- **`frontend/` is a sub-project.** Its own `package.json`, lockfile, lint, type-check, test pipeline. Built artefacts in `dist/` are served as static by FastAPI in production.

---

## 5. Shared interfaces — [Complete]

The contracts every stage depends on. Stage 0 ships these as Pydantic models; subsequent stages import without modifying. Adding fields to existing models is a structural change requiring a dedicated stage.

Cross-references to design-doc sections are authoritative; this section is the implementation slice.

### 5.1 Path canonicalisation (`shared/paths.py`)

```python
from pathlib import Path

HAMMOCK_ROOT: Path = Path.home() / ".hammock"

def projects_dir() -> Path:
    return HAMMOCK_ROOT / "projects"

def project_dir(slug: str) -> Path:
    return projects_dir() / slug

def project_json(slug: str) -> Path:
    return project_dir(slug) / "project.json"

def jobs_dir() -> Path:
    return HAMMOCK_ROOT / "jobs"

def job_dir(job_slug: str) -> Path:
    return jobs_dir() / job_slug

def job_json(job_slug: str) -> Path:
    return job_dir(job_slug) / "job.json"

def stage_dir(job_slug: str, stage_id: str) -> Path:
    return job_dir(job_slug) / "stages" / stage_id

def stage_json(job_slug: str, stage_id: str) -> Path:
    return stage_dir(job_slug, stage_id) / "stage.json"

def hil_item_path(job_slug: str, item_id: str) -> Path:
    return job_dir(job_slug) / "hil" / f"{item_id}.json"

def messages_jsonl(job_slug: str, stage_id: str) -> Path:
    return stage_dir(job_slug, stage_id) / "agent0" / "messages.jsonl"

# ... and so on for every path in the design doc's storage layout
```

Every path-producing function must live here. Hardcoded paths anywhere else in the codebase are a CI failure.

### 5.2 Atomic writes (`shared/atomic.py`)

```python
from pathlib import Path
import json, os, tempfile
from pydantic import BaseModel

def atomic_write_text(path: Path, content: str) -> None:
    """Write to <path>.tmp, fsync, rename to <path>. Survives mid-write crash."""
    ...

def atomic_write_json(path: Path, model: BaseModel) -> None:
    """model.model_dump_json() + atomic_write_text."""
    ...

def atomic_append_jsonl(path: Path, model: BaseModel) -> None:
    """Single-line append + fsync. Append-only files only."""
    ...
```

### 5.3 Pydantic models (`shared/models/`)

The full schemas are in the design doc; this section names the canonical surface and the file each lives in. Implementations follow the design doc verbatim.

| Model | File | Source in design doc |
|---|---|---|
| `Project`, `ProjectConfig` | `project.py` | § Project Registry |
| `JobState`, `JobConfig`, `JobCostSummary` | `job.py` | § Lifecycle § Job state machine; § Accounting Ledger |
| `StageDefinition`, `StageRun`, `StageState`, `Budget`, `ExitCondition`, `LoopBack` | `stage.py` | § Stage as universal primitive |
| `TaskRecord`, `TaskState` | `task.py` | § Lifecycle § Task state machine |
| `HilItem`, `AskQuestion`, `AskAnswer`, `ReviewQuestion`, `ReviewAnswer`, `ManualStepQuestion`, `ManualStepAnswer` | `hil.py` | § HIL bridge § HIL typed shapes |
| `Event` envelope + per-`event_type` payload models | `events.py` | § Observability § The event stream — typed taxonomy |
| `Plan`, `PlanStage` (`plan.yaml` shape) | `plan.py` | § Stage as universal primitive § The expander pattern |
| `PresentationBlock`, `UiTemplate` | `presentation.py` | § Presentation plane § Form pipeline; design-doc job templates |
| `AgentDef`, `SkillDef`, `SpecialistCatalogue`, `MaterialisedSpawn` | `specialist.py` | § Job templates, agents, skills, and hooks § Specialist resolution |
| `ReviewVerdict` (canonical schema) | `verdict.py` | § Plan Compiler § Review pattern and verdict schema |

### 5.4 MCP tool signatures (locked in design doc § HIL bridge § MCP tool surface)

```python
async def open_task(stage_id: str, task_spec: str, worktree_branch: str) -> dict[str, str]:
    """Returns {task_id}. Non-blocking."""

async def update_task(task_id: str, status: TaskState, result: dict | None = None) -> dict[str, bool]:
    """Returns {ok}. Non-blocking."""

async def open_ask(
    kind: Literal["ask", "review", "manual-step"],
    stage_id: str,
    task_id: str | None,
    **kind_specific_fields,
) -> HilAnswer:
    """Long-poll. Blocks until human answers."""

async def append_stages(stages: list[StageDefinition]) -> dict[str, int]:
    """Returns {ok, count}. Used by expander stages."""
```

### 5.5 Event envelope (locked in design doc § Observability § Event stream)

```python
class Event(BaseModel):
    seq: int
    timestamp: datetime
    event_type: str
    source: Literal["job_driver", "agent0", "subagent", "dashboard", "engine", "human", "hook"]
    job_id: str
    stage_id: str | None = None
    task_id: str | None = None
    subagent_id: str | None = None
    parent_event_seq: int | None = None
    payload: dict
```

### 5.6 HTTP API contract

OpenAPI is the contract. FastAPI generates `openapi.json`; `openapi-typescript` consumes it; `frontend/src/api/schema.d.ts` is the result. Routes are pinned in design doc § Presentation plane § URL topology. Stage 9 implements them; stage 10 adds SSE; no other stage edits routing.

### 5.7 Stage `presentation:` block

```yaml
presentation:
  ui_template: design-spec-review-form
  summary: "Design-spec for ${job.title} is ready for review."
```

`ui_template` resolves to a JSON declaration:

```
~/.hammock/ui-templates/<name>.json                  # global default (kernel)
<project_repo_root>/.hammock/ui-templates/<name>.json # per-project override (tunable)
```

Stage 13 implements the resolver and `FormRenderer`.

---

## 6. Stage map — [Complete]

Seventeen stages (0–16). Each produces one PR. The DAG below shows hard dependencies (a stage needs its parents merged); branches are independent and can run in parallel.

```
                         ┌──────────────────────────────────────────────────┐
                         │   Stage 0  Scaffold + shared/ models            │
                         └──────────────────────┬───────────────────────────┘
                                                │
                         ┌──────────────────────┴───────────────────────────┐
                         │   Stage 1  Storage layer + cache + watchfiles   │
                         └──┬─────────────────────────┬─────────────────┬──┘
                            │                         │                 │
       ┌────────────────────┼─────────────────────────┼─────────────────┘
       │                    │                         │
       ▼                    ▼                         ▼
 ┌───────────────┐  ┌───────────────┐    ┌──────────────────────┐
 │ Stage 2       │  │ Stage 3       │    │ Stage 8              │
 │ Project       │  │ Plan          │    │ FastAPI shell +      │
 │ Registry CLI  │  │ Compiler      │    │ cache wiring         │
 └───────┬───────┘  └───────┬───────┘    └──────────┬───────────┘
         │                  │                       │
         │                  ▼                       ▼
         │         ┌───────────────┐    ┌──────────────────────┐
         │         │ Stage 4       │    │ Stage 9              │
         │         │ Job Driver    │    │ HTTP API             │
         │         │ (state mach.) │    │ read endpoints       │
         │         └───────┬───────┘    └──────────┬───────────┘
         │                 │                       │
         │                 ▼                       ▼
         │         ┌───────────────┐    ┌──────────────────────┐
         │         │ Stage 5       │    │ Stage 10             │
         │         │ CLI session   │    │ SSE delivery + replay│
         │         │ + extraction  │    └──────────┬───────────┘
         │         └───────┬───────┘               │
         │                 │                       │
         │                 ▼                       │
         │         ┌───────────────┐               │
         │         │ Stage 6       │               │
         │         │ MCP server    │               │
         │         └───────┬───────┘               │
         │                 │                       │
         │                 ▼                       │
         │         ┌───────────────┐               │
         │         │ Stage 7       │               │
         │         │ HIL plane     │               │
         │         └───────┬───────┘               │
         │                 │                       │
         │                 │     ┌─────────────────┘
         │                 │     │
         │                 ▼     ▼
         │         ┌───────────────────────────────┐
         │         │  ── frontend track parallel ──│
         │         └───────────────┬───────────────┘
         │                         │
         │                         ▼
         │         ┌───────────────────────────────┐
         │         │ Stage 11  Frontend scaffold + │  (parallel-safe with 1+ onward)
         │         │ router + design system        │
         │         └───────┬───────────────────────┘
         │                 │
         │                 ▼
         │         ┌───────────────┐    (depends on 9 + 11)
         │         │ Stage 12      │
         │         │ Read views    │
         │         └───────┬───────┘
         │                 │
         │                 ▼
         │         ┌───────────────┐    (depends on 7 + 12)
         │         │ Stage 13      │
         │         │ Form pipeline │
         │         │ + HIL forms   │
         │         └───────┬───────┘
         │                 │
         │                 ▼
         │         ┌───────────────┐    (depends on 3 + 13)
         │         │ Stage 14      │
         │         │ Job submit UI │
         │         └───────┬───────┘
         │                 │
         │                 ▼
         │         ┌───────────────┐    (depends on 10 + 14)
         │         │ Stage 15      │
         │         │ Stage live    │
         │         │ view + Agent0 │
         │         │ stream pane   │
         │         └───────┬───────┘
         │                 │
         └─────────────────┼─────────────────┐
                           ▼                 │
                   ┌───────────────────────────────┐
                   │ Stage 16  E2E smoke +         │
                   │ self-host dogfood             │
                   └───────────────────────────────┘
```

### Parallelism matrix

After Stage 1 merges, the following groups are dispatch-parallel:

- **Group A (CLI + control plane):** 2 → 3 → 4 → 5 → 6 → 7
- **Group B (HTTP + real-time):** 8 → 9 → 10
- **Group C (frontend foundation):** 11 (independent of A and B for initial scaffold)
- **Group D (frontend integration):** 12 (after 9 + 11) → 13 (after 7 + 12) → 14 (after 3 + 13) → 15 (after 10 + 14)

At maximum, Stages 2, 8, and 11 can dispatch immediately after 1 — three agents in flight simultaneously. Stage 5 and Stage 9 can run in parallel once their dependencies (4 and 8 respectively) are merged.

### Critical path

`0 → 1 → 3 → 4 → 5 → 6 → 7 → 13 → 14 → 15 → 16`. Eleven stages on the critical path. If each stage is ~1–3 days of agent work, v0 lands in ~3–5 weeks of orchestrator time, less if dispatching parallel groups.

### Decisions captured here

- **Stages are PR-sized, not theme-sized.** No "frontend stage" or "backend stage" — every stage has a single specific deliverable.
- **No mid-stage fanout.** A stage's task DAG is internal to the stage's PR. Tasks within a stage parallelise per the design doc's quartet pattern; *stages* parallelise at the orchestrator level.
- **Frontend can start at Stage 11 without waiting for backend depth.** The OpenAPI types are generated fresh at any point Stage 9 lands; the frontend's stub period uses hand-mocked types until then.

---

## 7. Stages

Each stage section follows a fixed template:

**Goal** — what works after the PR merges.
**Depends on** — stages that must be merged first.
**Parallel with** — stages that can dispatch alongside.
**Files changed** — PR scope (hard limit).
**Task DAG** — `T1: define-interfaces` (when needed), `T2 || T3: write-tests || write-impl`, `T4: verify`. Mirrors design doc's quartet pattern.
**Acceptance criteria** — concrete checks the verify task runs.
**Out of scope** — what to defer to later stages.

---

### Stage 0 — Scaffold + shared models — [Spec complete]

**Goal.** Repo is bootstrappable. `uv sync && pnpm install` works. All Pydantic models in `shared/models/` exist with validation. CI runs lint + type-check + tests on push.

**Depends on.** Nothing.

**Parallel with.** Nothing (this is the root).

**Files changed.**
```
pyproject.toml, uv.lock, .python-version, .gitignore, .pre-commit-config.yaml
README.md
shared/__init__.py, shared/models/*.py, shared/paths.py, shared/atomic.py,
  shared/slug.py, shared/predicate.py
tests/conftest.py, tests/shared/**
.github/workflows/backend.yml
docs/design.md (copy from PDF source), docs/implementation.md (this file)
```

**Task DAG.**

- **T1 (interfaces):** transcribe every Pydantic model from the design doc into `shared/models/`, faithful to field names and types. Discriminated unions for `HilQuestion`/`HilAnswer`. Path helpers in `shared/paths.py`. Atomic write helpers. Slug derivation (test against the design doc's worked examples). Predicate evaluator stub (Stage 3 wires it up).
- **T2 (tests, parallel with T3):** for every model, factory + roundtrip-validation test + at-least-one rejection test (invalid input). Property tests for slug derivation. Property tests for path canonicalisation (no double slashes, no hardcoded `~`).
- **T3 (impl, parallel with T2):** same module set; T2 and T3 agents read each other's PR drafts.
- **T4 (verify):** `uv run pytest tests/shared/ -q` passes. `uv run pyright shared/` passes strict. `uv run ruff check .` passes. CI workflow runs on the PR. Sample script `scripts/manual-smoke-stage0.py` instantiates one of every model and prints them.

**Acceptance criteria.**

- All Pydantic models in design doc § HIL bridge, § Observability, § Stage as universal primitive, § Project Registry, § Plan Compiler, § Lifecycle exist and validate.
- `from shared.paths import job_dir; job_dir("foo")` returns `~/.hammock/jobs/foo`.
- `atomic_write_json(path, model)` produces a file containing `model.model_dump_json()`.
- 100% type-checked; no `Any` outside test files; no `# type: ignore`.
- CI passes on the PR.

**Out of scope.** No business logic. No CLI. No network. Pure data + helpers.

---

### Stage 1 — Storage layer + cache + watchfiles — [Spec complete]

**Goal.** A `Cache` object that bootstraps from disk and stays in sync via `watchfiles`. Given a `~/.hammock/` directory, the cache reflects every file in it as typed Pydantic objects. File changes propagate within 100ms.

**Depends on.** Stage 0.

**Parallel with.** Nothing (downstream of 0; everything else is downstream of 1).

**Files changed.**
```
dashboard/state/cache.py
dashboard/state/pubsub.py
dashboard/watcher/tailer.py
dashboard/__init__.py, dashboard/settings.py
tests/dashboard/state/**, tests/dashboard/watcher/**
```

**Task DAG.**

- **T1 (interfaces):** define `Cache` API — `bootstrap(root: Path) -> Cache`, `get_project(slug) -> Project | None`, `list_projects() -> list[Project]`, `get_job(slug) -> Job | None`, `list_jobs(project: str | None = None) -> list[Job]`, `get_stage(job, sid) -> StageRun | None`, `get_hil(item_id) -> HilItem | None`, `apply_change(path: Path, kind: ChangeKind)`. Define `InProcessPubSub` API — `subscribe(scope: str) -> AsyncIterator[Event]`, `publish(scope: str, event: Event)`.
- **T2 (tests):** integration tests using `tmp_path` fixtures. Create files on disk → wait briefly → assert cache reflects them. Modify → re-assert. Delete → assert removal. Property test: any sequence of valid file-system changes leaves the cache consistent with disk.
- **T3 (impl):** `Cache` reads `~/.hammock/` recursively at bootstrap, dispatches based on path pattern (matches against `shared.paths` helpers' inverses), populates typed dicts. `tailer.py` runs `watchfiles.awatch()` on `~/.hammock/`, dispatches each change to `cache.apply_change()`, then `pubsub.publish(scope_for(path), event)`. PubSub uses `asyncio.Queue` per subscriber; handles unsubscribe on cancel.
- **T4 (verify):** smoke script `scripts/smoke-stage1.py` — boots a cache against a fixture directory, prints all projects/jobs, then asynchronously watches for changes for 30s while the test creates fake files. Assert change events arrive.

**Acceptance criteria.**

- Bootstrap of a directory containing 100 jobs completes in <500ms.
- A new file under `~/.hammock/jobs/<slug>/job.json` is reflected in `cache.get_job(slug)` within 100ms of write.
- An invalid (non-parseable) JSON file logs an error but does not crash the cache.
- PubSub: subscribers get every event for their scope; non-subscribed scopes receive nothing.
- Memory: cache holds one Pydantic instance per file; no copies.

**Out of scope.** No HTTP. No CLI. No projections (those land in Stage 9). No real Job Driver writes (Stage 4).

---

### Stage 2 — Project Registry CLI — [Spec complete]

**Goal.** `hammock project register/list/info/rename/deregister/doctor` work. Commands write `~/.hammock/projects/<slug>/project.json` and create the override skeleton at `<repo>/.hammock/`.

**Depends on.** Stage 1.

**Parallel with.** Stages 3, 8, 11 (all reading/writing different parts of `~/.hammock/`).

**Files changed.**
```
cli/__init__.py, cli/__main__.py, cli/project.py, cli/doctor.py
tests/cli/**
```

**Task DAG.**

- **T1 (interfaces):** typer command group; subcommand signatures; doctor result types (full / light tiers, severity levels).
- **T2 (tests):** CliRunner-style tests against `tmp_path` HAMMOCK_ROOT. Each subcommand has a happy-path test plus 2–3 error-path tests (collision, invalid path, missing repo). Slug-collision prompt tested with input injection.
- **T3 (impl):** per design doc § Project Registry. Atomic writes via `shared.atomic`; path derivation via `shared.paths`; slug derivation via `shared.slug`. `.hammock/` skeleton creation; gitignore append; symlinks per design doc.
- **T4 (verify):** smoke script registers `tests/fixtures/toy-repo/` as a project, lists, runs doctor, deregisters. Each verb exits 0.

**Acceptance criteria.**

- `hammock project register /path/to/repo` produces `~/.hammock/projects/<derived-slug>/project.json` and `<path>/.hammock/{agent-overrides,skill-overrides,ui-templates}/`.
- Slug derivation matches the worked examples in the design doc.
- Doctor runs in <2s for the full tier; <200ms for the light tier.
- Deregister surfaces consequences-preview before destructive action.
- All commands have `--help` text generated from typer docstrings.

**Out of scope.** No GUI. No actual Hammock job operations. Doctor doesn't yet check Job Driver liveness (Stage 4 adds that).

---

### Stage 3 — Plan Compiler — [Spec complete]

**Goal.** `hammock job submit --project <slug> --type <type> --title <text>` runs the compiler synchronously and creates `~/.hammock/jobs/<job_slug>/{job.json, prompt.md, stage-list.yaml}`. Compile errors return structured failure with line refs.

**Depends on.** Stage 1, Stage 2.

**Parallel with.** Stages 8, 11.

**Files changed.**
```
dashboard/compiler/{compile.py, overrides.py, validators.py}
~/.hammock/job-templates/{build-feature.yaml, fix-bug.yaml}  # checked in under hammock/templates/
hammock/templates/job-templates/                             # ships with hammock; copied to ~/.hammock/ on first run
cli/job.py
tests/dashboard/compiler/**
```

**Task DAG.**

- **T1 (interfaces):** `compile(project_slug: str, job_type: str, title: str, request_text: str) -> CompileResult`. CompileResult variants: `Success(job_slug: str, job_dir: Path)` or `Error(failures: list[CompileFailure])`. Override merger signature; validator chain signature.
- **T2 (tests):** unit tests per validator rule; integration tests compiling `build-feature` and `fix-bug` templates against a test project with overrides; error-path tests for every failure mode in design doc § Plan Compiler § Validation rules.
- **T3 (impl):** template loader (`yaml.safe_load`); modify-only deep-merge with structural validation; param binder (`${job.slug}`, etc.); validator chain. Atomic writes via `shared.atomic`. Predicate evaluator (referenced by validators).
- **T4 (verify):** smoke script submits a `build-feature` job for the toy-repo project; asserts `job.json`, `prompt.md`, `stage-list.yaml` all exist and validate against schemas; runs the same submission with a deliberate override that fails validation, asserts a structured error.

**Acceptance criteria.**

- Both v0 templates (`build-feature`, `fix-bug`) compile cleanly against an empty-overrides project.
- Override merge is modify-only — adding/removing/reordering stages is rejected with a clear error message.
- The `runs_if` predicate grammar from § Stage as universal primitive parses and evaluates.
- All writes are atomic; a Ctrl-C mid-compile leaves no partial files.
- `hammock job submit ... --dry-run` returns the would-be plan without writing.

**Out of scope.** No Job Driver invocation (Stage 4). No classifier / hybrid composer (v1+).

---

### Stage 4 — Job Driver (state machine) — [Spec complete]

**Goal.** A subprocess that, given a `job_slug`, executes the job's stage list deterministically. State transitions persist to `job.json`; events to `events.jsonl`; heartbeat to `heartbeat`. *Stage execution is stubbed* — a fake stage runner that simulates a Claude Code session by reading inputs and writing outputs from a fixture script. Stage 5 swaps the fake for a real one.

**Depends on.** Stage 3.

**Parallel with.** Stages 8, 11.

**Files changed.**
```
job_driver/__init__.py, job_driver/__main__.py, job_driver/runner.py
job_driver/stage_runner.py            # interface + FakeStageRunner
dashboard/driver/{supervisor.py, lifecycle.py, ipc.py}
tests/job_driver/**, tests/dashboard/driver/**
```

**Task DAG.**

- **T1 (interfaces):** `JobDriver.run(job_slug)`. State machine transitions per design doc § Lifecycle § Job state machine. `StageRunner` protocol (`async def run(stage_def: StageDefinition, work_dir: Path) -> StageResult`); `FakeStageRunner` honors a `tests/fixtures/fake-runs/<stage_id>.yaml` script. `Supervisor` API: heartbeat checks, restart policy, command-file polling for cancellation.
- **T2 (tests):** state-machine transition tests (table-driven); supervisor heartbeat-stale detection; cancellation via signal; cancellation via command file; recovery after simulated crash (truncate `events.jsonl` mid-stream, reboot driver, assert resume).
- **T3 (impl):** straightforward Python state machine; `asyncio.create_subprocess_exec` for spawning stage runs (real or fake); heartbeat coroutine; SIGTERM handler; command-file poller. Supervisor tracks PIDs and heartbeat ages.
- **T4 (verify):** smoke script: submit a build-feature job (Stage 3 capability), launch driver, watch the fake stage runner cycle through all 12 spec'd stages, assert `job.json` lands in `COMPLETED`. Time-bounded to 60s.

**Acceptance criteria.**

- All state-machine transitions per design doc § Lifecycle § Job state machine § Transitions.
- `FakeStageRunner` produces realistic-shaped artefacts (markdown specs, plan.yaml, etc.) following the templates.
- Heartbeat written every 30s; supervisor declares stale at 90s.
- Cancel-via-SIGTERM completes within 5s and writes a `cancelled` end-state.
- Driver crash mid-stage: surviving artefacts preserved; resume picks up from current stage.
- No real Claude Code subprocess invoked yet.

**Out of scope.** Real agent sessions (Stage 5). MCP server (Stage 6). HIL (Stage 7).

---

### Stage 5 — CLI session spawning + observability extraction — [Spec complete]

**Goal.** Replace `FakeStageRunner` with `RealStageRunner` that spawns `claude` as subprocess, streams `--output-format stream-json`, parses into `messages.jsonl` + `tool-uses.jsonl` per design doc § Observability. Includes `Stop` hook validation. End of this stage: a real Claude Code session produces real artefacts on disk.

**Depends on.** Stage 4.

**Parallel with.** Stages 8, 9, 10, 11 (frontend track).

**Files changed.**
```
job_driver/stage_runner.py                  # add RealStageRunner alongside FakeStageRunner
job_driver/stream_extractor.py
hammock/hooks/validate-stage-exit.sh        # ships with hammock; symlinked at session spawn
tests/job_driver/test_stream_extractor.py
tests/job_driver/test_real_stage_runner.py  # uses recorded stream-json fixtures
tests/fixtures/recorded-streams/*.jsonl
```

**Task DAG.**

- **T1 (interfaces):** `RealStageRunner(StageRunner)`. `StreamExtractor.extract(stream_jsonl_path) -> ExtractedStream` producing `messages.jsonl`, `tool-uses.jsonl`, `result.json`, per-subagent demuxed dirs. Hook script invocation contract.
- **T2 (tests):** stream-extractor unit tests against ~10 recorded stream-json fixtures (real Claude Code outputs captured for this stage's tests). Subagent demux tests. Hook integration test (mock hook).
- **T3 (impl):** subprocess spawn with `--channels dashboard` (stub channel for now — Stage 6 wires up MCP); stream-json line-buffered parse; demux by `subagent_id`; atomic appends to jsonl files. Hook orchestration on `Stop` event.
- **T4 (verify):** end-to-end smoke against the toy-repo project — submit a job, run with `RealStageRunner`, watch the first stage (`write-problem-spec`) actually execute via Claude Code and produce `problem-spec.md`. Verify `messages.jsonl` and `tool-uses.jsonl` contain real entries.

**Acceptance criteria.**

- A real `claude` subprocess runs to completion for at least one stage type.
- `stream.jsonl` is the unmodified raw output; `messages.jsonl` and `tool-uses.jsonl` are derivations.
- Subagent demuxing produces correct per-subagent files when the agent dispatches subagents.
- `Stop` hook runs and its result is honored (failed validators block stage exit).
- `result.json` captures session-end summary (cost, tokens, exit code).

**Out of scope.** MCP tools (Stage 6). HIL (Stage 7). Anything beyond stages that can run agent-only without MCP calls.

---

### Stage 6 — MCP server (4 tools) — [Spec complete]

**Goal.** Dashboard MCP server exposes `open_task`, `update_task`, `open_ask`, `append_stages` over stdio. Spawned per active stage; agent's session can call them. `nudges.jsonl` writer + `--channels` push working. After this stage, mid-stage HIL works programmatically (no UI yet).

**Depends on.** Stage 5.

**Parallel with.** Stages 8, 9, 10, 11.

**Files changed.**
```
dashboard/mcp/{server.py, manager.py, channel.py}
job_driver/stage_runner.py                  # wire MCP server lifecycle
tests/dashboard/mcp/**
tests/e2e/test_mcp_round_trip.py
```

**Task DAG.**

- **T1 (interfaces):** four tool functions per signatures in § 5.4. `MCPManager.spawn(stage_id) -> server_handle`, `MCPManager.dispose(handle)`. `Channel.push(stage_id, message)` writing to `nudges.jsonl` and triggering `--channels` send.
- **T2 (tests):** unit tests per tool, mocking storage. `open_ask` long-poll test (creates HilItem, asserts blocked, simulates answer, asserts return). Concurrent tools test.
- **T3 (impl):** MCP Python SDK setup; tool registrations; per-stage stdio process spawn; long-poll implementation via `asyncio.Event`. Integration with cache: `open_task` writes `task.json`, cache picks up via watchfiles, returns `task_id`.
- **T4 (verify):** integration test launches a fake stage that calls `open_task`, then `update_task(DONE)`, then `open_ask(kind="ask", ...)`. Test simulates a human answer by writing to the HilItem file. Assert the agent receives the answer.

**Acceptance criteria.**

- All four tools work end-to-end against a real (test) session.
- `open_ask` blocks until the HilItem is answered or cancelled.
- `nudges.jsonl` accumulates entries; agent receives them at next turn.
- Per-stage MCP server process spawned on stage start, disposed on stage exit.
- Tool errors surface as MCP errors, not silent failures.

**Out of scope.** Stage 7 turns the bare HilItem files into a real HIL plane with state machine and orphan sweeping.

---

### Stage 7 — HIL plane realisation — [Spec complete]

**Goal.** HIL state machine + contract module + orphan sweeper. `dashboard.hil.contract.get_open_items()` and `submit_answer()` work. After this stage, the HIL Domain is complete; only the Transport (HTTP forms) is missing.

**Depends on.** Stage 6.

**Parallel with.** Stages 8, 9, 10, 11.

**Files changed.**
```
dashboard/hil/{state_machine.py, contract.py, orphan_sweeper.py}
tests/dashboard/hil/**
```

**Task DAG.**

- **T1 (interfaces):** `get_open_items(filter: HilFilter | None = None) -> list[HilItem]`, `submit_answer(item_id: str, answer: HilAnswer) -> HilItem`. State machine transitions: `awaiting → answered`, `awaiting → cancelled`. Sweeper: on stage restart, all `awaiting` items for that stage become `cancelled`.
- **T2 (tests):** transition tests; concurrent answer attempts (idempotency); sweeper invocation on simulated stage restart.
- **T3 (impl):** thin layer over the cache + `shared.atomic` writes; sweeper hooked into Job Driver's stage-restart path (callback wiring in Stage 4's supervisor).
- **T4 (verify):** end-to-end: launch fake stage, agent calls `open_ask`, contract returns the open item, contract submits answer, agent unblocks. Restart path: agent crashes mid-`open_ask`, sweeper cancels orphan, new agent creates fresh ask.

**Acceptance criteria.**

- State machine transitions match design doc § HIL bridge § HIL lifecycle.
- `get_open_items()` returns all `awaiting` items with optional filter (kind, stage, project).
- `submit_answer()` is idempotent — second submission with same answer is a no-op; with different answer is rejected.
- Orphan sweeper invoked exactly once per stage restart.
- No imports from `dashboard/api/` (Domain/Transport split — verified by import-linter rule in CI).

**Out of scope.** HTTP form rendering (Stage 13).

---

### Stage 8 — FastAPI shell + cache wiring — [Spec complete]

**Goal.** `python -m hammock.dashboard` starts uvicorn, FastAPI lifespan boots cache + watcher + pubsub. `GET /api/health` returns `{"ok": true, "cache_size": N}`. No business endpoints yet.

**Depends on.** Stage 1.

**Parallel with.** Stages 2, 3, 4, 5, 6, 7, 11.

**Files changed.**
```
dashboard/__main__.py, dashboard/app.py, dashboard/settings.py
dashboard/api/__init__.py        # router aggregation skeleton
tests/dashboard/test_app.py
.github/workflows/backend.yml    # extend to cover dashboard
```

**Task DAG.**

- **T1 (interfaces):** lifespan signature; settings (env-driven port, host, hammock-root override). Health response model.
- **T2 (tests):** TestClient-based `GET /api/health` returns 200 with expected payload. Lifespan startup/shutdown clean (no warnings, no leaked tasks).
- **T3 (impl):** lifespan context manager per design doc; `pydantic-settings` for config; rich logging.
- **T4 (verify):** `python -m hammock.dashboard` starts; curl `localhost:8765/api/health` returns OK; SIGTERM shuts down cleanly within 3s.

**Acceptance criteria.**

- Server starts in <1s on a fresh hammock-root.
- Cache, watcher, and pubsub are all instantiated and accessible via `app.state`.
- Graceful shutdown cancels all background tasks.
- No tests rely on real network — all in-process.

**Out of scope.** Any actual API endpoints (Stage 9). SSE (Stage 10). Frontend serving (Stage 11).

---

### Stage 9 — HTTP API read endpoints + projections — [Spec complete]

**Goal.** All read endpoints from § 5.6 work. Frontend can fetch `/api/projects`, `/api/jobs/<slug>`, `/api/stages/<sid>`, `/api/hil`, `/api/artifacts/...`, `/api/costs?...`. Projection layer transforms cache shapes into view shapes.

**Depends on.** Stage 8.

**Parallel with.** Stage 10, Stage 11.

**Files changed.**
```
dashboard/api/{projects.py, jobs.py, stages.py, hil.py, artifacts.py, costs.py, observatory.py}
dashboard/state/projections.py
tests/dashboard/api/**, tests/dashboard/state/test_projections.py
```

**Task DAG.**

- **T1 (interfaces):** every endpoint signature (path, params, response model). Every projection function (`project_list_item`, `job_list_item`, `job_detail`, `stage_detail`, `active_stage_strip_item`, `hil_queue_item`, `cost_rollup`, `system_health`).
- **T2 (tests):** TestClient suite — happy path + 404 + invalid-param per endpoint. Projection unit tests with fixture cache states. Schema-roundtrip via OpenAPI auto-generation.
- **T3 (impl):** thin route handlers calling projection functions. Cost rollups fold over `cost_accrued` events from cache.
- **T4 (verify):** smoke: bootstrap a fixture hammock-root with 3 projects, 5 jobs, 12 stages, 4 HIL items. Hit every endpoint. Compare responses against golden JSON fixtures.

**Acceptance criteria.**

- Every endpoint in design doc § Presentation plane § URL topology § HTTP API exists and matches its declared response model.
- `/openapi.json` is consumable by `openapi-typescript` (verified by running it as part of the test).
- Projections are pure functions of cache state.
- 404 / 422 responses follow FastAPI conventions.
- No endpoint exceeds 50ms response time on the fixture data.

**Out of scope.** SSE (Stage 10). Write endpoints (covered piecemeal in 13/14/15).

---

### Stage 10 — SSE delivery + replay — [Spec complete]

**Goal.** `/sse/global`, `/sse/job/<slug>`, `/sse/stage/<slug>/<sid>` deliver events in real-time. `Last-Event-ID` reconnect replays missed events from on-disk jsonl.

**Depends on.** Stage 9.

**Parallel with.** Stage 11.

**Files changed.**
```
dashboard/api/sse.py
dashboard/state/pubsub.py        # extend with replay-from-disk path
tests/dashboard/api/test_sse.py
tests/dashboard/state/test_pubsub_replay.py
```

**Task DAG.**

- **T1 (interfaces):** SSE handler signature (FastAPI `StreamingResponse` with `text/event-stream`). Replay function: `replay_since(scope: str, last_event_id: int) -> AsyncIterator[Event]`. Wire format per design doc § Real-time delivery.
- **T2 (tests):** SSE smoke (subscribe, write file change, assert event arrives). `Last-Event-ID` replay test (subscribe, disconnect, append events to disk, reconnect with `Last-Event-ID`, assert all missed events arrive in order).
- **T3 (impl):** SSE handler subscribes to pubsub, formats events per § Real-time wire format, sends keepalive every 15s. Replay reads from on-disk jsonl files filtered by scope, emits matching events with `seq > last_event_id`.
- **T4 (verify):** curl-based test: connect to `/sse/stage/<slug>/<sid>`, watch events arrive as files change. Disconnect with `Last-Event-ID: 42`, reconnect, observe replay.

**Acceptance criteria.**

- New events arrive within 200ms of disk write.
- Reconnect with `Last-Event-ID` replays correctly; no events lost, none duplicated.
- A slow client doesn't block other subscribers.
- Per-scope isolation: subscribers to `/sse/job/A` get no `/sse/job/B` events.

**Out of scope.** Frontend consumption (Stage 11+).

---

### Stage 11 — Frontend scaffold + router + design system — [Spec complete]

**Goal.** Vite project bootstrapped. All routes from § 5.6 navigate (showing stub views). Tailwind set up. `openapi-typescript` running against Stage 9's OpenAPI. Pinia stores skeleton. SSE wrapper composable. Design tokens defined.

**Depends on.** Stage 1 (just for repo structure).

**Parallel with.** Stages 2–10. **Note:** the OpenAPI sync step is a no-op until Stage 9 lands; agent uses hand-mocked schema until then.

**Files changed.**
```
dashboard/frontend/package.json, pnpm-lock.yaml, vite.config.ts, tsconfig.json,
  tailwind.config.ts, postcss.config.js, index.html
dashboard/frontend/src/main.ts, App.vue, router.ts
dashboard/frontend/src/api/{schema.d.ts, client.ts, queries.ts}
dashboard/frontend/src/stores/{global.ts, projects.ts, jobs.ts, hil.ts}
dashboard/frontend/src/sse.ts
dashboard/frontend/src/views/                 # one stub per route
dashboard/frontend/src/components/shared/     # design tokens + StateBadge + CostBar + MarkdownView
dashboard/frontend/src/components/nav/        # top bar, side nav
.github/workflows/frontend.yml
```

**Task DAG.**

- **T1 (interfaces):** route table; Pinia store contracts; SSE composable signature (`useEventStream(scope: string)`); design tokens (colors, spacing, typography).
- **T2 (tests):** vitest unit tests for SSE composable (mocked EventSource), Pinia store mutations, `StateBadge` rendering each state class, `CostBar` budget threshold logic.
- **T3 (impl):** scaffold via Vite Vue+TS template; configure Tailwind; lazy-load route components; Pinia store skeleton; SSE wrapper using `useEventSource` from `@vueuse/core` plus custom replay logic.
- **T4 (verify):** `pnpm dev` runs; navigate to every route; each shows a stub page matching the design intent; lint + type-check + unit tests all pass in CI.

**Acceptance criteria.**

- All 11 routes navigate without errors.
- Tailwind classes work.
- Type-checking `pnpm tsc --noEmit` passes strict.
- Stub pages show route name + a "TODO" indicator; no real data fetching yet.
- Build (`pnpm build`) produces a `dist/` consumable by FastAPI in prod.

**Out of scope.** Real data integration (Stage 12). Forms (Stage 13). Live stage view (Stage 15).

---

### Stage 12 — Read views (project, job, artifact, HIL queue, cost, settings) — [Spec complete]

**Goal.** Six of eleven views show real data. Project list, project detail, job overview, artifact viewer, HIL queue, cost dashboard, settings — all fully functional read-only.

**Depends on.** Stage 9, Stage 11.

**Parallel with.** Stages relevant only to the agent track (5/6/7) if those are still in flight.

**Files changed.**
```
dashboard/frontend/src/views/{Home.vue, ProjectList.vue, ProjectDetail.vue,
  JobOverview.vue, ArtifactViewer.vue, HilQueue.vue, CostDashboard.vue, Settings.vue}
dashboard/frontend/src/api/queries.ts        # extend with all reads
dashboard/frontend/src/components/{stage/StageTimeline.vue, shared/MarkdownView.vue, ...}
dashboard/frontend/tests/unit/views/**
```

**Task DAG.**

- **T1 (interfaces):** per-view data contract (which queries each view fires); component composition tree.
- **T2 (tests):** mounted-component tests with mocked TanStack Query data; per-view rendering tests for empty / populated / error states.
- **T3 (impl):** TanStack Query hooks per view; SSE patches via Pinia (subscribe at view mount, patch the relevant query cache on each event).
- **T4 (verify):** smoke against running backend with fixture data; click through every view; verify renders match design intent (per design doc § View inventory).

**Acceptance criteria.**

- All six views render real backend data.
- SSE updates flow through to UI within 300ms.
- Empty states and error states are handled.
- Markdown renders with GFM features (tables, code blocks, syntax highlighting).
- Cost dashboard charts render and respect scope toggle.

**Out of scope.** HIL forms (Stage 13). Stage live view (Stage 15). Job submit form (Stage 14).

---

### Stage 13 — Form pipeline + HIL forms — [Spec complete]

**Goal.** `/hil/<item_id>` renders a form for each `kind`. Submit flows back to `dashboard.hil.contract.submit_answer`. Eight v0 templates implemented as JSON declarations. Per-project override resolution works.

**Depends on.** Stage 7, Stage 12.

**Parallel with.** Stage 14 (different surfaces but both write paths).

**Files changed.**
```
dashboard/api/hil.py                                 # add POST /api/hil/<id>/answer
dashboard/frontend/src/views/HilItem.vue
dashboard/frontend/src/components/forms/{FormRenderer.vue, AskForm.vue,
  ReviewForm.vue, ManualStepForm.vue, TemplateRegistry.ts}
hammock/templates/ui-templates/{design-spec-review-form.json, impl-spec-review-form.json,
  impl-plan-spec-review-form.json, integration-test-review-form.json,
  pr-merge-form.json, spec-review-form.json,
  ask-default-form.json, manual-step-default-form.json}
tests/dashboard/api/test_hil_post.py
dashboard/frontend/tests/unit/forms/**
```

**Task DAG.**

- **T1 (interfaces):** template JSON schema; `FormRenderer` props; submit endpoint contract; per-project override resolution rules.
- **T2 (tests):** template-loading tests (global, project-override, both, neither); FormRenderer rendering each kind; submit-roundtrip tests.
- **T3 (impl):** template registry on backend (resolves per-project-first); `FormRenderer` reads template, fetches `context_artifacts` via `/api/artifacts/...`, mounts the right kind-specific form; submit calls `POST /api/hil/<id>/answer`.
- **T4 (verify):** end-to-end with a real HIL item — agent creates ask via Stage 6's MCP server, browser navigates to HIL form, human submits, agent unblocks.

**Acceptance criteria.**

- All three kinds (`ask`, `review`, `manual-step`) render and submit correctly.
- All eight v0 templates present and well-formed.
- Per-project override at `<repo>/.hammock/ui-templates/<name>.json` resolves before global.
- Override modifies text/context fields only; cannot change `kind` or answer schema.
- Optimistic submit + error handling.

**Out of scope.** Soul-proposed templates (v2+).

---

### Stage 14 — Job submit + Plan Compiler integration — [Spec complete]

**Goal.** `/jobs/new` form launches a real job. Compiler runs server-side; on success, redirect to job overview. Compile errors surface inline.

**Depends on.** Stage 3, Stage 13.

**Parallel with.** Stage 15.

**Files changed.**
```
dashboard/api/jobs.py                       # add POST /api/jobs (compile + spawn driver)
dashboard/frontend/src/views/JobSubmit.vue
dashboard/frontend/src/components/jobs/{JobTypeRadio.vue, SlugPreview.vue, DryRunPreview.vue}
dashboard/driver/lifecycle.py               # wire spawn-driver-on-submit
tests/dashboard/api/test_job_submit.py
dashboard/frontend/tests/unit/views/test_job_submit.py
```

**Task DAG.**

- **T1 (interfaces):** submit endpoint request/response shape; dry-run flag.
- **T2 (tests):** submit happy path; submit with compile error → 422 with structured failures; dry-run returns plan without writing.
- **T3 (impl):** `POST /api/jobs` calls `compiler.compile()`; on success, spawns Job Driver via `dashboard.driver.lifecycle`; returns `{job_slug}`. Frontend form drives this with live slug derivation, project picker (from `/api/projects`), and dry-run preview.
- **T4 (verify):** browser submits a build-feature job for the toy-repo project. Driver spawns. Job overview shows STAGES_RUNNING state. Watch first stage execute in the browser (using already-existing job overview from Stage 12).

**Acceptance criteria.**

- Form validates slug derivation client-side.
- Submit → redirect to `/jobs/<slug>` within 1s of compile success.
- Compile errors render inline, pinned to offending fields where possible.
- Dry-run produces the plan without writing or spawning.
- Driver spawn confirmed via supervisor heartbeat appearing in the cache.

**Out of scope.** Live stage view (Stage 15).

---

### Stage 15 — Stage live view + Agent0 stream pane — [Spec complete]

**Goal.** `/jobs/<slug>/stages/<sid>` shows the live three-pane view per design doc. Agent0 stream pane works end-to-end: chronological merge, virtualised render, subagent expand/collapse, chat input that goes through `--channels`, in-stage HIL inline forms, cancel/restart actions. **The single most complex view in the system.**

**Depends on.** Stage 10, Stage 14.

**Parallel with.** Nothing.

**Files changed.**
```
dashboard/api/{stages.py, chat.py}            # POST /chat, POST /cancel, POST /restart
dashboard/frontend/src/views/StageLive.vue
dashboard/frontend/src/components/stage/{Agent0StreamPane.vue, SubAgentRegion.vue,
  ToolCall.vue, ProseMessage.vue, EngineNudge.vue, HumanChat.vue, AgentReply.vue,
  ChatInput.vue, StreamFilters.vue, TasksPanel.vue, BudgetBar.vue}
dashboard/frontend/src/composables/useAgent0Stream.ts        # the merge algorithm
tests/dashboard/frontend/unit/components/stage/**
tests/e2e/test_stage_live_view.py             # Playwright smoke
```

**Task DAG.**

- **T1 (interfaces):** Agent0StreamPane props; six leaf-component contracts; `useAgent0Stream(stageId)` composable signature; merge algorithm spec; auto-scroll-with-anchor state model.
- **T2 (tests):** merge algorithm property tests (interleaving, out-of-order tolerance, idempotency); virtualised renderer tests; subagent-region expand/collapse; chat input optimistic flow; filter tests.
- **T3 (impl):** the composable manages four SSE source streams + reconciles into one sorted timeline; six leaf components per design doc § Agent0 stream pane; vue-virtual-scroller wrap; auto-scroll-with-anchor state machine; chat input with optimistic + 5s confirmation.
- **T4 (verify):** Playwright e2e — submit a fix-bug job, navigate to live stage view, watch real Agent0 prose + tool calls + subagent dispatches stream in. Send a chat message; assert the agent receives it (verify by checking `nudges.jsonl`). Cancel the stage; assert clean shutdown.

**Acceptance criteria.**

- Merge algorithm correctly orders events from four sources by timestamp.
- Out-of-order events within 500ms window are placed correctly.
- Virtualised renderer handles 10k+ entries without frame drops.
- Subagent regions expand inline and via dedicated pane (tab switcher).
- Chat input optimistic state shows pending → confirmed → error.
- Cancel writes to command file; driver picks up; stage transitions to CANCELLED within 5s.

**Out of scope.** Soul / Council surfaces (v2+).

---

### Stage 16 — E2E smoke + self-host dogfood — [Spec complete]

**Goal.** Hammock runs Hammock. Register hammock as a project under hammock; submit a real `fix-bug` job for a known toy bug; watch through to PR. Plus formal E2E test suite covering the critical path.

**Depends on.** Stage 15.

**Parallel with.** Nothing.

**Files changed.**
```
tests/e2e/test_full_lifecycle.py                # end-to-end Playwright + backend integration
tests/fixtures/dogfood-bug/                     # a real toy bug to fix
docs/runbook.md                                 # how to operate hammock
README.md                                       # update with quickstart
.github/workflows/e2e.yml                       # nightly run
```

**Task DAG.**

- **T1 (interfaces):** none new; this stage is integration.
- **T2 (tests):** full-lifecycle test — register, submit, watch all 12+ stages flow, answer HILs, see PR open, merge in test mode (don't actually push to upstream), verify completion.
- **T3 (impl):** test orchestration; toy-bug fixture; runbook authoring.
- **T4 (verify):** orchestrator runs the dogfood manually: register hammock as a project, submit a real fix-bug for a real (intentionally-introduced-then-recorded) bug. Walk through the full lifecycle. Confirm PR opened. Document any rough edges discovered as v1+ backlog items.

**Acceptance criteria.**

- Full lifecycle test runs in CI and passes.
- Manual dogfood produces a merge-ready PR.
- Runbook covers: install, first-run, register-project, submit-job, common operations, troubleshooting.
- README quickstart works on a fresh machine.
- v1+ backlog updated with discovered rough edges.

**Out of scope.** Anything explicitly deferred-by-design (Telegram, Soul, Council, the four other job templates, etc.).

---

## 8. Cross-cutting concerns — [Complete]

### 8.1 Testing strategy

Three tiers, each with a different role:

- **Unit tests** (per stage). Live next to the code they test. Run on every commit. Target sub-second total.
- **Integration tests** (Stages 4, 6, 7, 9, 10, 13, 14). Cross-module within one process. Use `tmp_path` for hammock-root.
- **E2E tests** (Stages 5, 15, 16). Real subprocesses, real Claude Code (with API mocking optional via `ANTHROPIC_API_KEY=fake` for stage-5-style fixture-based tests). Only run on PRs touching backend or frontend integration code; nightly full run.

Recorded fixtures (Stage 5 onwards) capture real Claude Code stream-json output for replay in tests. New fixtures land in `tests/fixtures/recorded-streams/` whenever a new tool or response shape is added.

### 8.2 CI for hammock itself

Three GitHub Actions workflows:

- `backend.yml`: lint (ruff), type (pyright), test (pytest). Triggered on changes to Python paths.
- `frontend.yml`: lint (ESLint), type (`tsc --noEmit`), test (vitest). Triggered on changes to `dashboard/frontend/`.
- `e2e.yml`: Playwright + backend integration. Triggered on PRs touching either; nightly full run on main.

Each workflow caches its respective dependency manager (uv cache for Python, pnpm store for frontend).

### 8.3 Conventions

- **Commit style:** Conventional Commits. Scope is `stage-NN` for stage PRs.
- **Branch naming:** `feat/stage-NN-<short-name>`. Squash-merge into main; preserve full commit history in the PR for review.
- **PR title:** `feat(stage-NN): <stage name>`.
- **PR description:** must reference the design doc sections relied on, the implementation doc stage section, and a short "verify" subsection showing the smoke output.
- **Tagging:** after each stage merges, tag `v0.<stage-num>`. After Stage 16, tag `v0.16` and start v1+ work.

### 8.4 Parallel-stage execution recipe

Stages whose dependencies have all merged can execute concurrently. The mechanic is **one Claude Code session per parallel stage, each in its own git worktree**. No coordination tooling beyond git itself.

**Worktree naming:** `hammock-stage-NN` (e.g., `~/workspace/hammock-stage-08`).

**Setup (one command per parallel stage):**

```bash
cd ~/workspace/hammock
git worktree add ~/workspace/hammock-stage-NN main
cd ~/workspace/hammock-stage-NN
claude
```

Each session takes a starting stage and runs through its sequential chain (e.g., session for Stage 2 continues into Stages 3–7 once each merges).

**Conflict story:**

- Code conflicts on parallel stages are near-zero by design: the layout (§4) gives each stage a disjoint subtree (`cli/`, `dashboard/api/`, `dashboard/frontend/`, etc.). `shared/` is locked post-Stage 0; nothing writes there.
- Conflicts that DO appear are mechanical:
  - `pyproject.toml` — each stage adds dependencies (union the additions).
  - `uv.lock` — regenerated by `uv sync` after rebase.
  - `docs/stages/README.md` — index table; append the new row.
  - `README.md` — only if multiple stages touch the same line.
- **Resolution policy:** the later-merging session rebases. Agent runs `git fetch origin && git rebase origin/main`, resolves mechanical conflicts, runs `uv sync`, reruns the verify suite, then `git push --force-with-lease`. CI re-runs; PR ready.
- **Semantic conflicts** (rare): agent halts the rebase, posts a comment on the PR explaining the conflict, and waits for human direction. Does not ship a guessed fix.

### 8.5 TDD discipline within a stage

Every stage follows tests-first within a single session. Two intermediate commits make the discipline visible in the PR diff:

```
commit 1:  test(stage-NN): failing tests for <feature>
commit 2:  feat(stage-NN): impl that makes tests pass
commit 3:  test(stage-NN): edge + property coverage   (optional, only if added)
```

Squash-merge collapses these into a single `feat(stage-NN): ...` commit on `main`. The PR review can still see the trail.

**Fuzzy-spec stages** (Stage 4 Job Driver state machine, Stage 7 HIL orphan sweeper, Stage 13 form pipeline override merge, Stage 15 Agent0 stream merge algorithm) MAY escalate to a T2||T3 sub-agent split — the main agent dispatches one Explore agent for tests against the interface and one for impl, then integrates. For all other stages, single-agent tests-first is the default.

### 8.6 Per-stage summary docs

Every merged stage adds `docs/stages/stage-NN.md` with the following structure:

- **What was built** — externally visible capability.
- **Notable design decisions** made during implementation that aren't in `design.md` or `implementation.md`.
- **Locked for downstream stages** — invariants future stages can rely on.
- **Files added/modified** — full inventory.
- **Dependencies introduced** — package + version + purpose.
- **Acceptance criteria — met** — checklist mirroring §7's stage spec.
- **Notes for downstream stages** — anything a future stage's author should know that isn't obvious from the code.

The summary lands as a small commit on `main` directly *after* the stage PR merges (not in the stage PR itself), so the stage PR shows just code while the summary captures retrospective context. `docs/stages/README.md` is the index; append a row per merged stage.

This doc is intended to be enough — alongside `design.md` and `implementation.md` — for a future agent to start the next stage without re-deriving prior decisions.

### 8.7 Decisions captured here

- **Stages can interleave.** When two parallel stages land within hours of each other, their merges into main are independent — no rebase storm because each touches a disjoint files set. The repo structure is designed for this.
- **One Claude Code session per parallel stage.** Worktrees keep their state separate; merges into main are git-native.
- **Tests-first within a stage.** Two intermediate commits (`test:` then `feat:`) preserve the discipline trail in the PR diff; squash-merge collapses for clean history.
- **T2||T3 sub-agent split is the exception, not the rule.** Used only for fuzzy-spec stages.
- **Stage summary docs are mandatory** and land as small commits on `main` after the stage PR merges.
- **No long-lived feature branches.** A stage that doesn't fit in one PR splits into sub-stages with intermediate stage numbers (e.g., 12a, 12b). Don't accumulate.
- **No mid-stage scope creep.** If an agent finds a missing piece partway through a stage, it surfaces it (PR comment, separate issue) and the orchestrator decides whether to split or push to v1+.
- **Test fixtures are committed.** Recorded stream-json, fake-stage YAML scripts, golden API JSON. These are part of the contract, not transient.
- **Frontend can ship before backend integration.** Stage 11 lands with stub views; Stage 12 backfills real data. This keeps the frontend agent productive in parallel with backend work.

---

## 9. After v0 — [Complete]

The v1+ deferred-by-design list from the design doc rolls forward unchanged. To be picked up after v0 is dogfooded:

- Plan Compiler classifier (path c) and hybrid composer
- Structured-ops override semantics (`add_stage_after`, `remove_stage`)
- Telegram notification carrier
- Soul concrete realisation (proposer prompt, cadence, evidence-bundling)
- Council concrete realisation (convener, reviewers, static checks, blast-radius classifier)
- Cross-project Soul learning
- Job branch → main automation
- Agent0 mid-stage checkpointing
- Project-shipped agent integration at `<repo>/.claude/agents/`
- The four remaining job templates (`refactor`, `migration`, `chore`, `research-spike`)
- Filesystem-level scoping for task subagents

The first v1+ stage will reuse this doc's structure: stage numbers continue (Stage 17, 18, ...) but tagged `v1.*`. The deferred items themselves don't have a fixed order; expect to pick whichever is most pressing once dogfooding reveals which ones are missed first.

---

## 10. Iteration log

| Date | Change |
|---|---|
| 2026-05-02 | Initial implementation plan. 17 stages (0–16) covering full v0 scope. Tech stack pinned to specific versions. Repo structure finalised. Shared interfaces enumerated with cross-references to design doc. Stage map drawn as a DAG with parallelism matrix and critical path. Per-stage specs include goal, dependencies, parallelism, file scope, task DAG (test/impl/verify), acceptance criteria, and out-of-scope. Cross-cutting concerns: testing strategy, CI, conventions. v1+ items rolled forward unchanged from design doc. |
| 2026-05-02 | Stages 0 and 1 shipped (PRs #1 and #2). Conventions added to §8: parallel-stage execution recipe (worktrees named `hammock-stage-NN`, one Claude Code session per parallel stage, conflict resolution policy with mechanical-rebase by later-merging session and halt-for-human on semantic conflicts), TDD discipline (tests-first within a stage; intermediate commits `test:` then `feat:` visible in PR diff; T2\|\|T3 sub-agent split reserved for fuzzy-spec stages 4/7/13/15), and mandatory per-stage summary docs at `docs/stages/stage-NN.md` landing as small commits on `main` after the stage PR merges. |
