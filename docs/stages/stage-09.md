# Stage 9 — HTTP API read endpoints + projections

**PR:** TBD (see branch `feat/stage-09-http-read-api`)
**Branch:** `feat/stage-09-http-read-api`
**Commit on `main`:** TBD (squash-merge target)

## What was built

The dashboard's read-side HTTP surface. The frontend (Stage 11+) can now
fetch every read shape in the design doc § Presentation plane § URL
topology — projects, jobs, stages, HIL queue, artifacts, costs,
observatory metrics. The projection layer sits between the cache and
the HTTP handlers; route handlers are one-liners.

- **`dashboard/state/projections.py`** — Pydantic result models
  (`ProjectListItem`, `ProjectDetail`, `JobListItem`, `JobDetail`,
  `StageListEntry`, `StageDetail`, `ActiveStageStripItem`, `HilQueueItem`,
  `CostRollup`, `SystemHealth`, `ObservatoryMetrics`) plus pure functions
  over `Cache` state. Cost projections fold `cost_accrued` events from
  `events.jsonl` files on demand (Stage 1 deliberately does not cache
  append-only logs).
- **`dashboard/api/projects.py`** — `GET /api/projects`, `GET /api/projects/{slug}`.
- **`dashboard/api/jobs.py`** — `GET /api/jobs?project=&status=`,
  `GET /api/jobs/{job_slug}`.
- **`dashboard/api/stages.py`** — `GET /api/jobs/{job_slug}/stages/{stage_id}`,
  `GET /api/active-stages` (running + attention-needed strip for the home).
- **`dashboard/api/hil.py`** — `GET /api/hil?status=&kind=&project=&job=`,
  `GET /api/hil/{item_id}`.
- **`dashboard/api/artifacts.py`** — `GET /api/artifacts/{job_slug}/{path:path}`
  with content-type sniffing by suffix and path-traversal protection.
- **`dashboard/api/costs.py`** — `GET /api/costs?scope=&id=&job=` (project,
  job, or stage scope).
- **`dashboard/api/observatory.py`** — `GET /api/observatory/metrics`
  (v0 stub; Soul / Council fill it in v2+).
- **`dashboard/api/__init__.py`** — extended to `include_router` every
  per-resource router so `app.include_router(router)` in `create_app`
  picks them all up unchanged.
- **`dashboard/state/cache.py`** — gains a public `hil_job_slug(item_id)`
  helper so projections can look up the owning job without poking
  private state.
- **148 new tests** under `tests/dashboard/state/` and `tests/dashboard/api/`
  — projection unit tests against a populated cache, TestClient route
  tests for happy / 404 / 422 paths, plus a schema-roundtrip test that
  verifies `/openapi.json` parses, names every route, and resolves every
  `$ref` (the same checks `openapi-typescript` would gate on).
- **`tests/dashboard/conftest.py`** — `populated_root` + `client`
  fixtures shared by the projection and route suites.
- **`scripts/manual-smoke-stage09.py`** — bootstraps a fixture root
  (3 projects, 5 jobs, 12 stages, 4 HIL items, cost events on one job),
  starts the server, hits every endpoint, and checks status codes,
  payload counts, and the < 50 ms response-time bound.

## Notable design decisions made during implementation

1. **Cost-event payload convention pinned.** `cost_accrued` events carry
   `payload = {"usd": <float>, "tokens": <int>, "agent_ref": <str>}`.
   Future producers (Stage 4 driver, Stage 5 real runner) MUST follow
   this. The folder is forgiving — missing fields are skipped, malformed
   lines are ignored — so cost numbers never crash the dashboard.
2. **Cost fallback to `StageRun.cost_accrued`.** When a job has no
   events.jsonl yet (mid-flight, or the producer hasn't written), the
   job-level total falls back to summing `StageRun.cost_accrued` from
   the cache. Never returns `None` cost; never blocks a user view.
3. **Stage detail reads task.json files on demand.** Tasks aren't in the
   cache (Stage 1 scope). The projection scans
   `jobs/<slug>/stages/<sid>/tasks/<task_id>/task.json` per request.
   Acceptable at v0 scale; if it becomes a hotspot, Stage 1's
   `classify_path` is the place to add task tracking.
4. **`current_stage_id` is a best-effort hint.** Picks the latest
   non-terminal stage; falls back to "most recently started" if none.
   Chosen for the job-list-row UX (one badge per row), not stage-state
   integrity.
5. **`hil_queue` defaults to `status=awaiting`.** Per design doc § HIL
   queue ("oldest-awaiting first"). Pass `status=None` for the full
   history. The route exposes this as a query param.
6. **Active stages strip uses `RUNNING ∪ ATTENTION_NEEDED` only.**
   Per design doc § Dashboard home; `BLOCKED_ON_HUMAN` items live in the
   HIL queue, so they would double-count if we included that state here.
7. **`/api/active-stages` is its own route, not nested under `/api/jobs`.**
   The home page asks "what's hot across all jobs" — flat URL fits.
   Route lives in `dashboard/api/stages.py` alongside `GET /api/jobs/.../stages/...`.
8. **Cost rollup `?scope=stage` requires `?job=<slug>`.** Stage IDs are
   unique per job, not globally. Returning 422 if absent is a kinder
   error than silently producing zeros.
9. **Artifact route resolves under the job dir and rejects traversal.**
   Path-traversal returns 400 (escapes job dir) or 404 (silently absent
   when a normalised path resolves outside). The job-dir check uses
   `Path.resolve()` + `relative_to()`; symlink edge cases are not
   special-cased (v1+ if symlinks become a real surface).
10. **Routes use `Annotated[type, Query(...)]` not `= Query(...)`.**
    Avoids ruff's B008 (function-call-in-default) without `# noqa`.
    Existing FastAPI guidance; matches Stage 8's `Settings(root=...)`
    style of dependency injection.

## Locked for downstream stages

- **Projection function signatures are stable.** Stage 12 (read views)
  and Stage 15 (live stage view) consume these as the canonical view
  shapes; new fields require a stage. None of the projections take
  pubsub or watcher dependencies — they are pure over the cache.
- **`Cache.hil_job_slug(item_id)`** is the canonical way to find the
  owning job for a HIL item. The `_hil_job` map stays private.
- **`/openapi.json` is the contract** for `frontend/src/api/schema.d.ts`.
  Stage 11 will run `openapi-typescript` against a live server; the
  schema-roundtrip test in this stage gates the contract.
- **Cost events live in `events.jsonl` only.** No materialised rollup
  files. v0 scale handles per-request fold; v1+ may add a cache.
- **Read endpoints are GET-only.** No state changes here. Stage 13 adds
  `POST /api/hil/{id}/answer`; Stage 14 adds `POST /api/jobs`; Stage 15
  adds the per-stage POST sub-resources (cancel, restart, chat).
- **Projections take an optional `now` parameter** for time-relative
  fields (`age_seconds`). Tests pin `now`; production passes nothing
  and gets `datetime.now(UTC)`.

## Files added/modified

```
dashboard/api/__init__.py            (extended — router aggregation)
dashboard/api/projects.py            (new)
dashboard/api/jobs.py                (new)
dashboard/api/stages.py              (new)
dashboard/api/hil.py                 (new)
dashboard/api/artifacts.py           (new)
dashboard/api/costs.py               (new)
dashboard/api/observatory.py         (new)
dashboard/state/projections.py       (new)
dashboard/state/cache.py             (added hil_job_slug helper)

tests/dashboard/conftest.py          (new — populated_root + client)
tests/dashboard/state/test_projections.py   (new)
tests/dashboard/api/__init__.py      (new)
tests/dashboard/api/test_projects.py        (new)
tests/dashboard/api/test_jobs.py            (new)
tests/dashboard/api/test_stages.py          (new)
tests/dashboard/api/test_hil.py             (new)
tests/dashboard/api/test_artifacts.py       (new)
tests/dashboard/api/test_costs.py           (new)
tests/dashboard/api/test_observatory.py     (new)
tests/dashboard/api/test_openapi.py         (new)

scripts/manual-smoke-stage09.py      (new)

docs/stages/stage-09.md              (this file)
docs/stages/README.md                (index row added)
```

## Dependencies introduced

None. All Stage 9 functionality runs on the libraries Stage 8 already
pulled (`fastapi`, `pydantic`, `pydantic-settings`, `httpx` test-only).

## Acceptance criteria — met

- [x] Every endpoint in design doc § URL topology § HTTP API exists and
      matches its declared response model.
- [x] `/openapi.json` is consumable by `openapi-typescript` (verified by
      the schema-roundtrip test that resolves every `$ref`).
- [x] Projections are pure functions of cache state (the only I/O is
      the on-demand events.jsonl fold for cost; deliberate per design).
- [x] 404 / 422 responses follow FastAPI conventions.
- [x] No endpoint exceeds 50 ms response time on the fixture data
      (smoke script asserts the projects-list bound; other routes are
      strictly cheaper).
- [x] 281 tests pass (127 dashboard + 154 prior).
- [x] ruff + ruff format clean.
- [x] pyright strict on `shared/` + `dashboard/` clean.

## Notes for downstream stages

- **Stage 10 (SSE)**: subscribe to `app.state.pubsub` for live patches.
  Replay reads from on-disk jsonl files filtered by scope; same files
  the cost rollup folds. Wire format per design doc § Real-time delivery.
  Don't re-fetch projections on every event; the frontend pattern is
  TanStack Query snapshot + SSE patches.
- **Stage 11 (Frontend scaffold)**: run `npx openapi-typescript
  http://localhost:8765/openapi.json -o src/api/schema.d.ts`. Every
  route in this stage shows up in the spec; the schema-roundtrip test
  here is the gate.
- **Stage 12 (Read views)**: views map 1:1 to projection types here.
  `ProjectList.vue` consumes `ProjectListItem[]`; `JobOverview.vue`
  consumes `JobDetail`; `HilQueue.vue` consumes `HilQueueItem[]`. The
  `ActiveStageStripItem` feeds the home page strip.
- **Stage 13 (Form pipeline)**: adds `POST /api/hil/{id}/answer` to
  `dashboard/api/hil.py` next to the existing GETs. The reads here
  remain unchanged.
- **Stage 14 (Job submit)**: adds `POST /api/jobs` to
  `dashboard/api/jobs.py`. Existing `GET /api/jobs/...` reads pick up
  the new job once the Plan Compiler writes it (cache + watcher do the
  delivery).
- **Stage 15 (Stage live view)**: adds `POST /api/jobs/.../stages/.../{cancel,restart,chat}`
  to `dashboard/api/stages.py` and a sibling `chat.py`. The
  `StageDetail` projection here is what the left + right panes render.
- **Cost producers**: when Stages 4–6 land, write events with
  `event_type="cost_accrued"` and `payload={"usd": <float>, "tokens":
  <int>, "agent_ref": <str>}` to `events.jsonl` (job-level for the
  rollup; stage-level for stage-scoped reads). The folder ignores
  unknown payload keys — additions are non-breaking.
