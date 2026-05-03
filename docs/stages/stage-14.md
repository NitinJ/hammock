# Stage 14 — Job submit + Plan Compiler integration

**PR:** [#18](https://github.com/NitinJ/hammock/pull/18) (open)
**Branch:** `feat/stage-14-job-submit`
**Commit on branch:** `6a0d304`

## What was built

The job submission pipeline. Users can now open `/jobs/new`, fill in the form, and either preview the compiled stage plan (dry run) or launch a real job that writes a job directory and spawns a driver subprocess. Compile errors — unknown project, unknown template, binding failures — surface inline in the form with structured failure objects.

### Backend

- **`POST /api/jobs`** — accepts `{project_slug, job_type, title, request_text, dry_run}`. Calls `compile_job()` from the Plan Compiler, which returns either a `CompileSuccess` or a `list[CompileFailure]`.
  - On compile failure: raises HTTP 422 with `detail: [{kind, stage_id, message}]` — a structured array, not FastAPI's default validation error shape.
  - On dry run success: returns `{job_slug, dry_run: true, stages: [...]}` — the full compiled stage list, no job directory written, no driver spawned.
  - On real submit: calls `spawn_driver(job_slug, root=...)` (double-forks a detached subprocess), returns `{job_slug, dry_run: false, stages: null}`.
- **`JobSubmitRequest`** — Pydantic model with `extra="forbid"` and `min_length=1` on all string fields.
- **`JobSubmitResponse`** — `{job_slug: str, dry_run: bool, stages: list[dict] | None}`.

### Frontend

- **`JobSubmit.vue`** (rewritten from stub) — full form with project selector, job-type radios, title input with slug preview, request textarea, dry-run checkbox, and submit button. On success, redirects to `/jobs/{job_slug}` (real submit) or shows the compiled stage list (dry run). Compile errors render inline.
- **`JobTypeRadio.vue`** (new) — `v-model` radio component with `build-feature` and `fix-bug` options.
- **`SlugPreview.vue`** (new) — derives the job slug client-side, mirroring server logic: lowercase → replace non-alphanum with `-` → collapse `--` → strip leading/trailing `-` → truncate to 32 chars → prepend `YYYY-MM-DD-`. Shown live as the user types the title.
- **`DryRunPreview.vue`** (new) — renders the compiled stage list as a numbered `<ol>` of `{id, description}` entries.

### Tests

- **21 Python API tests** in `tests/dashboard/api/test_job_submit.py`:
  - Happy path: 201, `job_slug` present, `dry_run: false`, `stages: null`, job directory exists, `spawn_driver` called with correct slug and root
  - Dry run: 201, `dry_run: true`, `stages` non-empty with `id` on each entry, no job directory written, `spawn_driver` not called
  - Compile failures: unknown project → 422 `project_not_found`, unknown job type → 422 `template_not_found`, all failures have `kind`, `stage_id`, `message`
  - Request body validation: empty required fields → 422, extra field → 422
- **13 frontend unit tests** in `dashboard/frontend/tests/unit/views/JobSubmit.spec.ts`:
  - Rendering: form not stub, project selector, job-type radios, title input, textarea, dry-run checkbox
  - Slug preview: updates live with typed title
  - Successful submit: redirects to `/jobs/{slug}`
  - Compile errors: renders error text, no redirect
  - Dry run: shows stage list, no redirect
- **612 Python tests** total (all passing); **127 frontend tests** total (all passing)

### Manual smoke script

- **`scripts/manual-smoke-stage14.py`** — three scenarios against a live dashboard process: dry-run → 201 with stages, unknown project → 422 `project_not_found`, unknown job type → 422 `template_not_found`.

## Notable design decisions made during implementation

1. **Structured 422 for compile failures, not FastAPI's default validation shape.** The compile failures are domain errors, not schema validation errors. Using `HTTPException(422, detail=[...])` with an explicit list lets the frontend pattern-match on `Array.isArray(body.detail)` and render each failure individually. FastAPI's built-in 422 (for Pydantic violations) uses `{detail: [{loc, msg, type}]}`; our compile 422 uses `{detail: [{kind, stage_id, message}]}`. These are distinguishable by field name.

2. **`spawn_driver` is mocked at the `dashboard.api.jobs` import site, not at the source module.** `unittest.mock.patch("dashboard.api.jobs.spawn_driver", ...)` patches the name as it exists in the module that uses it. This is the standard Python mocking pattern; patching the original `dashboard.driver.lifecycle.spawn_driver` would not intercept calls made through the already-imported reference.

3. **`stages: null` in the real-submit response, not `stages: []`.** An empty list would be ambiguous — did compilation produce no stages? Returning `null` makes it unambiguous: stages are only present on dry-run responses.

4. **Slug preview is computed client-side.** The server derives the slug the same way (via `shared.slug`). The client reimplements the same algorithm in TypeScript rather than making a round-trip on every keystroke. The date prefix uses `new Date().toISOString().slice(0, 10)` which matches the server's `datetime.date.today().isoformat()`.

5. **"Does not redirect" assertions use `vi.spyOn(router, "push")` rather than checking `router.currentRoute.value.path`.** `createWebHistory()` shares `window.history` across tests in jsdom — after a navigation in one test, subsequent tests start at the navigated URL. Switching to `createMemoryHistory("/jobs/new")` still starts at `/` (the argument sets a base prefix, not the initial location). Spying on `router.push` is the correct approach: it tests intent (the component should not navigate) rather than testing shared global state.

6. **`TestClient` does not need the context manager for submit tests.** `app.state.settings` is set in `create_app` before the lifespan fires. Only `app.state.cache` requires the lifespan (it's populated by the event store subscription). Submit tests don't access the cache, so `TestClient(app)` without `with` works. This was already established in Stage 13 (`test_hil_post.py`).

## Locked for downstream stages

- **`POST /api/jobs` request/response shape is stable.** `{project_slug, job_type, title, request_text, dry_run}` → `{job_slug, dry_run, stages}`. Downstream stages may add optional fields to the request but must not remove existing ones.
- **Compile 422 `detail` is a list of `{kind, stage_id, message}`.** Frontend and any API consumers depend on this shape. Do not change to FastAPI's default `{detail: [{loc, msg, type}]}` format.
- **`spawn_driver` is called exactly once on real submit with `(job_slug, root=settings.root)`.** Stage 15 (cancel/restart) will call `spawn_driver` again on restart; it should not assume it is only ever called from the submit path.
- **`DryRunPreview.vue` expects `stages: Array<{id?: string, description?: string}>`.** The shape comes from `StageDefinition.model_dump(mode="json")`. If `StageDefinition` adds fields, `DryRunPreview` will silently ignore them — that is fine.

## Files added/modified (12)

```
dashboard/api/jobs.py                                         (modified — POST endpoint, request/response models)
dashboard/frontend/src/views/JobSubmit.vue                    (modified — full implementation from stub)
dashboard/frontend/src/components/jobs/JobTypeRadio.vue       (new)
dashboard/frontend/src/components/jobs/SlugPreview.vue        (new)
dashboard/frontend/src/components/jobs/DryRunPreview.vue      (new)

tests/dashboard/api/test_job_submit.py                        (new — 21 tests)
dashboard/frontend/tests/unit/views/JobSubmit.spec.ts         (new — 13 tests)

scripts/manual-smoke-stage14.py                               (new)
docs/stages/stage-14.md                                       (new)
docs/stages/README.md                                         (modified — stage-14 row added)
```

## Acceptance criteria — met

- [x] `POST /api/jobs` with valid body → 201 + `{job_slug, dry_run: false, stages: null}` + job directory created + driver spawned
- [x] `POST /api/jobs` with `dry_run: true` → 201 + `{job_slug, dry_run: true, stages: [...]}`, no job directory, no driver
- [x] Unknown `project_slug` → 422 with `[{kind: "project_not_found", ...}]`
- [x] Unknown `job_type` → 422 with `[{kind: "template_not_found", ...}]`
- [x] Empty required field → 422 (Pydantic validation)
- [x] Extra field in body → 422 (`extra="forbid"`)
- [x] `JobSubmit.vue` form renders: project selector, job-type radios, title, request, dry-run toggle
- [x] Slug preview updates live as user types
- [x] Real submit redirects to `/jobs/{job_slug}`
- [x] Compile error displayed inline, no redirect
- [x] Dry-run result shows stage list, no redirect
- [x] 612 Python tests pass; 127 frontend tests pass; ruff + pyright clean

## Notes for downstream stages

- **Stage 15 (cancel/restart/chat sub-resources):** The `POST /api/jobs/{slug}/cancel` and `POST /api/jobs/{slug}/restart` endpoints should follow the same pattern: call a domain function (job lifecycle), return a structured response. `spawn_driver` for restart should re-use the same `job_slug` directory; the driver will check for an existing `job.yaml` and resume.
- **Job type enumeration:** `job_type` is currently a free-form string validated by the Plan Compiler (template resolution). If the frontend should offer a curated dropdown instead of free-form radios, add `GET /api/job-types` returning the list of available templates. The compiler's template resolver already knows how to enumerate them.
- **Stage list in dry-run response:** `stages` is `list[dict]` (serialized `StageDefinition`). If the frontend needs richer stage metadata (e.g., HIL kinds, expected artifacts), it is already available in the serialized dict — `DryRunPreview.vue` just doesn't display it yet.
