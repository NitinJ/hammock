# dashboard/frontend/

Vue 3 SPA. Built with Vite + TypeScript + Tailwind. State via vue-query (no Pinia store for server state). Live updates via SSE.

## Stack

- Vue 3 (Composition API only — no Options API).
- Vite + TypeScript.
- Tailwind CSS.
- `@tanstack/vue-query` — server state.
- `vue-router` — pages.
- `unified` + `remark-*` + `rehype-*` — markdown rendering for `document` field.
- Vitest — unit + component tests.
- Playwright — e2e against live dashboard.

## Layout

```
dashboard/frontend/
├── src/
│   ├── api/
│   │   ├── client.ts        # api.get/post/del wrappers; ApiError class
│   │   ├── queries.ts       # vue-query bindings (use* functions)
│   │   └── schema.d.ts      # TypeScript mirrors of the FastAPI Pydantic models
│   ├── components/
│   │   ├── hil/             # AskHumanDisplay, FormRenderer
│   │   ├── jobs/            # EnvelopeView, JobStreamPane, renderRows
│   │   └── shared/          # StateBadge, etc.
│   ├── composables/
│   │   └── useSse.ts        # EventSource wrapper, query invalidation
│   ├── lib/
│   │   └── markdown.ts      # unified pipeline for `document` field
│   ├── router/index.ts
│   ├── views/               # one file per page
│   │   ├── Projects.vue
│   │   ├── ProjectDetail.vue
│   │   ├── ProjectAdd.vue
│   │   ├── JobsList.vue
│   │   ├── JobOverview.vue  # the big two-pane page
│   │   ├── JobSubmit.vue
│   │   ├── HilQueue.vue
│   │   └── Settings.vue
│   ├── App.vue
│   ├── main.ts
│   └── style.css
├── tests/
│   ├── unit/                # Vitest
│   │   ├── AskHumanDisplay.spec.ts
│   │   ├── EnvelopeView.spec.ts
│   │   ├── FormRenderer.spec.ts
│   │   └── renderRows.spec.ts
│   └── e2e/                 # Playwright
│       ├── _seed.ts         # tmpdir HAMMOCK_ROOT seeding helpers
│       ├── hil_form_submit.spec.ts
│       └── two_pane_job_page.spec.ts
├── package.json
├── vite.config.ts
├── tsconfig.json
└── tailwind.config.js
```

## API access pattern

Always go through `api/queries.ts`:

```ts
const project = useProject(slug);            // GET
const submit = useSubmitJob();                // mutation
await submit.mutateAsync({...});
```

Query keys are centralized in `QUERY_KEYS`. When invalidating, do so by the centralized key, not by re-typing the array.

`schema.d.ts` is **manually maintained** to mirror the Pydantic models. When you add a new endpoint or change a model in `dashboard/api/`, update `schema.d.ts` in the same PR.

## Pages

| Page             | Path              | Notes                                                       |
|------------------|-------------------|-------------------------------------------------------------|
| Projects list    | `/projects`       | Lists registered projects with health status.               |
| Project detail   | `/projects/:slug` | Verify / delete; **Workflows section** (Stage 6 copy).      |
| Project add      | `/projects/add`   | Register a local repo path.                                 |
| Jobs list        | `/jobs`           | Filtered by repo + state.                                   |
| Job overview     | `/jobs/:slug`     | Two-pane: node tree on left, detail/HIL form on right.      |
| Job submit       | `/jobs/new`       | Project + workflow dropdowns + request text.                |
| HIL queue        | `/hil`            | All pending HIL across all jobs.                            |
| Settings         | `/settings`       | Read-only diagnostic page.                                  |

`JobOverview.vue` is the workhorse. It reads `useJob` for the node list, `useNodeDetail` for selected-node envelopes, `useHilQueue` for explicit HIL gates. Loop iterations unrolled via `components/jobs/renderRows.ts`.

## SSE

`composables/useSse.ts` opens an `EventSource` to `/sse/global` or `/sse/job/<slug>`. On each event, it calls `queryClient.invalidateQueries(...)` for the relevant key. Frontend never polls.

## Markdown rendering for `document`

Stage 2 added `document: str` (markdown) on narrative artifact types. `EnvelopeView.vue` detects the field and renders it via `lib/markdown.ts` (unified pipeline: parse → gfm → rehype → sanitize → highlight → stringify).

If you're adding a new envelope renderer, route through `EnvelopeView` — don't render markdown ad-hoc per page.

## Tests

```
pnpm test                # vitest run
pnpm test -- --watch     # vitest watch
pnpm test:e2e            # playwright (boots its own dashboard via vite dev + uvicorn)
pnpm type-check          # vue-tsc
pnpm lint                # eslint, max-warnings 0
pnpm format              # prettier --check
pnpm format:fix          # prettier --write
pnpm build               # vite build (CI gate; commonly missed)
```

Vitest tests mount components in isolation. Playwright tests boot a real dashboard against a tmp HAMMOCK_ROOT seeded by `tests/e2e/_seed.ts`.

## When you change a Pydantic model

1. Update `dashboard/api/...` Python.
2. Update `dashboard/frontend/src/api/schema.d.ts` to match.
3. Update vue-query bindings in `queries.ts` if the endpoint URL changed.
4. Update Playwright `_seed.ts` if the model is seeded for tests.

There's no auto-codegen for `schema.d.ts` (yet). Keep them manually in sync.

## When you add a new page

1. Create `views/X.vue`.
2. Register in `router/index.ts`.
3. Add to navigation if user-facing.
4. Add a Playwright test for the happy path if it has interactive elements.

## When you add a new envelope type with `document`

It works automatically — `EnvelopeView` detects `document` regardless of type. Just make sure the corresponding Pydantic type carries the field and the agent prompt instructs it.

## Detail docs

- `docs/for_agents/architecture.md` — backend ↔ frontend boundary, SSE event types.
- `docs/for_agents/testing.md` — Vitest vs Playwright, when to use which.
- `docs/for_agents/gotchas.md` — `pnpm build` is a gate; eslint v-html rule; format-then-lint order.
