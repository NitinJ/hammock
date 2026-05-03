# Stage 12 — Read views (project, job, artifact, HIL queue, cost, settings)

**Branch:** `feat/stage-12-read-views`
**Worktree:** `~/workspace/hammock-stage-12`

## What was built

Eight views wired with real TanStack Query data, replacing all `StubView` placeholders that landed in Stage 11. Every endpoint from Stage 9 now has a consuming UI view.

- **`Home.vue`** — active-stages strip (every `RUNNING`/`ATTENTION_NEEDED` stage card with job·stage·cost·state badge), HIL awaiting list, recent-jobs list. Subscribes to `/sse/global` via `useEventStream`.
- **`ProjectList.vue`** — project cards with `name`, `repo_path`, doctor-status badge (`green`/`yellow`/`red`/`unknown`), job count, open HIL count. Empty state handled.
- **`ProjectDetail.vue`** — project metadata section (repo path, remote URL, default branch) + scoped job list. Uses `useProject(slug)` + `useJobs(slug)` (slug reactive from route params).
- **`JobOverview.vue`** — job slug header, state badge, total cost. Embeds `StageTimeline` for the ordered stage list. Artifacts panel links to the five standard spec files. Per-stage cost breakdown table. Subscribes to `/sse/job/:slug`.
- **`ArtifactViewer.vue`** — fetches artifact content from `/api/artifacts/:jobSlug/:path` as raw text. `.md` files rendered via `MarkdownView`; `.yaml`/`.yml`/`.json` and "raw toggle" show `<pre>`. Loading/error states handled.
- **`HilQueue.vue`** — table of awaiting HIL items: item ID (link to `/hil/:id`), kind badge, job slug (link to `/jobs/:slug`), stage ID, age in seconds. Empty state: "No awaiting items right now."
- **`CostDashboard.vue`** — scope selector (`job`/`project`/`stage`) + ID text input driven by route query params. Renders `total_usd`, `total_tokens`, by-stage breakdown table, by-agent breakdown table via `useCosts(scope, id)`. Query disabled when ID is empty.
- **`Settings.vue`** — system health panel showing server `ok` status (green/red) and `cache_size` from `/api/health`. About blurb.

New components:
- **`StageTimeline.vue`** (`src/components/stage/`) — vertical list of `StageListEntry` rows with `RouterLink` → stage live view, `StateBadge`, cost, and duration (computed from `started_at`/`ended_at`). Empty state: "No stages yet."
- **`MarkdownView.vue`** (updated) — wired `unified + remark-parse + remark-gfm + remark-rehype + rehype-highlight + rehype-sanitize + rehype-stringify` async pipeline. Input processed via `watch(..., { immediate: true })`; sanitised before `v-html` injection.

Query layer (`queries.ts`) additions:
- `useProject(slug)` → `GET /api/projects/:slug` → `ProjectDetail`
- `useJob(jobSlug)` → `GET /api/jobs/:jobSlug` → `JobDetail`
- `useActiveStages()` → `GET /api/active-stages` → `ActiveStageStripItem[]`
- `useCosts(scope, id)` → `GET /api/costs?scope=&id=` → `CostRollup` (disabled when `id` is empty)
- `useArtifact(jobSlug, path)` → raw `fetch /api/artifacts/…` → `string`
- Fixed `useJobs(projectSlug)` — now honours the slug param (query key + URL param both reactive)
- Added `QUERY_KEYS.activeStages`, `QUERY_KEYS.artifact`

Schema (`schema.d.ts`) corrections (divergences from backend projections.py):
- `ProjectListItem`: replaced `active_job_count`/`cost_30d_usd` with `total_jobs`/`default_branch`
- `JobListItem`: replaced `title`/`cost_usd`/`budget_cap_usd` with `job_id`/`total_cost_usd`/`current_stage_id`
- `CostRollup`: replaced `breakdown: CostBreakdownEntry[]` with `total_tokens`/`by_stage`/`by_agent`
- Added: `ProjectConfig`, `ProjectDetail`, `JobConfig`, `JobDetail`, `StageListEntry`, `ActiveStageStripItem`, `HilQueueItem`, `SystemHealth`, `ObservatoryMetrics`, `DoctorStatus`

Config additions:
- `dashboard/frontend/.eslintrc.cjs` — ESLint flat config: `@typescript-eslint/recommended` + `plugin:vue/vue3-recommended` + `@tanstack/eslint-plugin-query/recommended`
- `dashboard/frontend/.prettierrc` — 100-char width, trailing commas, no semi override

## Notable design decisions made during implementation

1. **`useArtifact` fetches raw text, not JSON.** The artifact endpoint returns `text/*` for markdown/YAML/JSON; using the `api.get` JSON wrapper would fail. The hook calls `fetch` directly and reads `.text()`. The enabled guard prevents fetching when the path is empty.

2. **`MarkdownView` async pipeline — no `computed`.** `unified.process()` returns a `Promise`, making a synchronous `computed()` unusable. Instead, `watch(() => props.content, render, { immediate: true })` drives an async render function that writes to `rendered = ref("")`. No flash of escaped text: the ref starts empty and the first paint waits for the first tick.

3. **`StageTimeline` stays a dumb component.** It takes `stages: StageListEntry[]` and `jobSlug: string` as props; it does no fetching. `JobOverview` passes `detail.stages` directly. This made it trivially testable without a QueryClient.

4. **SSE subscription scope in views.** `Home.vue` subscribes to the `global` scope. `JobOverview.vue` subscribes to `job/:jobSlug`. For Stage 12 the `onEvent` handlers only call `globalStore.applyEvent` (HIL count tracking); full query-cache patching is deferred to Stage 15 when the live stage pane lands, because the main consumer of sub-300ms updates is the stage stream — not the overview.

5. **`CostDashboard` route-query initialisation.** The scope and ID are initialised from `route.query.scope`/`route.query.id` so deep links like `/costs?scope=job&id=feat-auth-20260501` pre-fill the form. Tests mock `useRoute` to supply these params, keeping test data self-contained.

6. **Schema divergences resolved by reading `projections.py` directly.** The hand-authored `schema.d.ts` from Stage 11 had several mismatches (wrong field names, extra/missing fields). Rather than running `pnpm schema:sync` (which needs a live server), the Stage 9 PR is "in PR" status, so fields were read directly from `dashboard/state/projections.py` and `shared/models/`. The sync script will confirm correctness once Stage 9 merges.

## Files added/modified

```
dashboard/frontend/src/api/schema.d.ts           (updated — type corrections + new types)
dashboard/frontend/src/api/queries.ts             (updated — 5 new hooks + useJobs fix)

dashboard/frontend/src/views/Home.vue             (replaced stub)
dashboard/frontend/src/views/ProjectList.vue      (replaced stub)
dashboard/frontend/src/views/ProjectDetail.vue    (replaced stub)
dashboard/frontend/src/views/JobOverview.vue      (replaced stub)
dashboard/frontend/src/views/ArtifactViewer.vue   (replaced stub)
dashboard/frontend/src/views/HilQueue.vue         (replaced stub)
dashboard/frontend/src/views/CostDashboard.vue    (replaced stub)
dashboard/frontend/src/views/Settings.vue         (replaced stub)

dashboard/frontend/src/components/stage/StageTimeline.vue   (new)
dashboard/frontend/src/components/shared/MarkdownView.vue   (wired remark pipeline)

dashboard/frontend/src/stores/jobs.ts             (cost_usd → total_cost_usd)
dashboard/frontend/.eslintrc.cjs                  (new)
dashboard/frontend/.prettierrc                    (new)

dashboard/frontend/tests/unit/views/Home.spec.ts            (new)
dashboard/frontend/tests/unit/views/ProjectList.spec.ts     (new)
dashboard/frontend/tests/unit/views/ProjectDetail.spec.ts   (new)
dashboard/frontend/tests/unit/views/JobOverview.spec.ts     (new)
dashboard/frontend/tests/unit/views/ArtifactViewer.spec.ts  (new)
dashboard/frontend/tests/unit/views/HilQueue.spec.ts        (new)
dashboard/frontend/tests/unit/views/CostDashboard.spec.ts   (new)
dashboard/frontend/tests/unit/views/Settings.spec.ts        (new)
dashboard/frontend/tests/unit/components/stage/StageTimeline.spec.ts  (new)
dashboard/frontend/tests/unit/stores/jobs.spec.ts           (updated fixture)

scripts/manual-smoke-stage12.py                   (new)
docs/stages/stage-12.md                           (this file)
docs/stages/README.md                             (row added)
```

## Dependencies introduced

```
unified                 (markdown processor)
remark-parse            (markdown AST parser)
remark-gfm              (GFM: tables, task lists, strikethrough)
remark-rehype           (remark → rehype bridge)
rehype-highlight        (syntax highlighting)
rehype-sanitize         (XSS-safe HTML sanitisation)
rehype-stringify        (rehype → HTML string)
```

## Acceptance criteria — met

- [x] All eight views (`Home`, `ProjectList`, `ProjectDetail`, `JobOverview`, `ArtifactViewer`, `HilQueue`, `CostDashboard`, `Settings`) render real backend data via TanStack Query.
- [x] SSE updates wired: `Home` → `global` scope; `JobOverview` → `job/:slug` scope.
- [x] Empty states and loading states handled for all views.
- [x] `StageTimeline` component renders stage list with state badges, cost, duration.
- [x] `MarkdownView` wired to `unified + remark-gfm + rehype-highlight` async pipeline; XSS-sanitised.
- [x] 73 tests pass (14 test files: 36 prior + 37 new).
- [x] `vue-tsc --noEmit` clean.
- [x] `vite build` clean.
- [x] `ruff check .` clean (Python stack unchanged).
- [x] `pyright shared/ dashboard/` — 0 errors.
- [x] `pytest tests/ -q` — all passing (unchanged).
- [x] `.eslintrc.cjs` + `.prettierrc` added so CI lint/format steps become hard failures.

## Notes for downstream stages

- **Stage 13 (HIL form pipeline):** `HilItem.vue` is still a stub (form rendering is Stage 13's scope). Import `HilItem` type (full shape with question/answer) from `schema.d.ts` — it's already defined. The `HilQueueItem` slim type is what the list view uses; `HilItem` is what the form needs.
- **Stage 14 (Job submit):** `JobSubmit.vue` is still a stub. `useProjects()` and the `QUERY_KEYS` registry are ready. When POST `/api/jobs` lands, add a `useMutation` alongside `useProjects` to submit.
- **Stage 15 (Stage live view):** `StageLive.vue` is still a stub. The SSE subscription in `JobOverview.vue` is intentionally minimal — Stage 15 owns the full per-event cache-patching logic (TanStack Query `setQueryData` per event type).
- **`pnpm schema:sync`:** Run once Stage 9 merges to regenerate `schema.d.ts` from the live `/openapi.json`. The hand-corrected types should match exactly; any divergence is a design inconsistency to resolve.
- **ArtifactViewer binary content:** For non-text suffixes, the view currently renders raw text. A "download" fallback for binary content (images, PDFs) can be added in a follow-up without touching any tests.
- **CostDashboard ECharts bars:** The design doc shows bar charts. Stage 12 ships tables as a functional baseline. Stage 15 installs `echarts` for the live pane; the cost bars can be added in the same PR without touching Stage 12's query layer.
