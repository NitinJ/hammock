# Stage 13 — Form pipeline + HIL forms

**PR:** TBD (open)
**Branch:** `feat/stage-13-form-pipeline`

## What was built

The HIL form pipeline. Human operators can now open a HIL item's detail page, see the right form pre-populated from a JSON template, and submit their answer — which is persisted to disk and unblocks the waiting job stage.

Three new backend endpoints, a backend `TemplateRegistry` module, three Vue form components, a `FormRenderer` dispatcher, an updated `HilItem.vue` view, and eight v0 JSON UI-template declarations.

### Backend

- **`dashboard/hil/template_registry.py`** — `TemplateRegistry` resolves named UI templates with per-project-first semantics. Resolution order: `<project_repo>/.hammock/ui-templates/<name>.json` (project override) → `<root>/ui-templates/<name>.json` (global default). Override may change `instructions`, `description`, and `fields` but must NOT change `hil_kinds` — doing so raises `TemplateKindConflictError`, which propagates to the API as HTTP 409. `TemplateNotFoundError` → HTTP 404.
- **`GET /api/hil/{id}`** — returns `HilItemDetail` envelope: `{item: HilItem, job_slug, project_slug, ui_template_name}`. `ui_template_name` is derived from item kind via `_KIND_DEFAULT_TEMPLATE` (v0; plan-based lookup deferred to v1).
- **`GET /api/hil/templates/{name}`** — resolves the named template via `TemplateRegistry`, applies per-project override if `project_slug` query param is provided.
- **`POST /api/hil/{id}/answer`** — validates `answer.kind == item.kind` (422 on mismatch), then delegates to `HilContract.submit_answer`. Idempotent for identical re-submits; 409 on conflicting re-submit; 404 when item not found.

### Frontend

- **`TemplateRegistry.ts`** — `fetchTemplate(name, projectSlug?)` fetches `GET /api/hil/templates/{name}` with optional `project_slug` param. Returns `null` on 404, throws on other errors.
- **`AskForm.vue`** — renders question text, radio buttons for `options` (when present), free-text textarea. `getAnswer()` returns `{kind:"ask", choice, text}`.
- **`ReviewForm.vue`** — renders review prompt and target artifact path, approve/reject radio buttons, comments textarea. `getAnswer()` returns `{kind:"review", decision, comments}`.
- **`ManualStepForm.vue`** — splits multi-line instructions, renders output textarea. `getAnswer()` returns `{kind:"manual-step", output, extras:null}`.
- **`FormRenderer.vue`** — routes to the correct form component via `v-if` on `item.item.kind`, renders `template.instructions` and `template.fields.extra_help`, uses `template.fields.submit_label ?? "Submit"`, emits `submit` with the answer from `formRef.value.getAnswer()`.
- **`HilItem.vue`** (updated from stub) — fetches `GET /api/hil/{itemId}` → `HilItemDetail`, calls `fetchTemplate(ui_template_name, project_slug)`, renders `FormRenderer` for awaiting items, shows "already answered" banner for non-awaiting, calls `POST /api/hil/{itemId}/answer` on submit, surfaces error text on POST failure.

### Templates

Eight JSON files in `hammock/templates/ui-templates/`, all conforming to `UiTemplate` schema (`name`, `description`, `hil_kinds`, `instructions`, `fields`):

| File | Kinds | Purpose |
|---|---|---|
| `ask-default-form.json` | `["ask"]` | Default form for ask-kind HIL items |
| `spec-review-form.json` | `["review"]` | Generic spec review |
| `design-spec-review-form.json` | `["review"]` | Design doc review |
| `impl-spec-review-form.json` | `["review"]` | Implementation spec review |
| `impl-plan-spec-review-form.json` | `["review"]` | Implementation plan review |
| `integration-test-review-form.json` | `["review"]` | Integration test plan review |
| `pr-merge-form.json` | `["review"]` | PR merge decision |
| `manual-step-default-form.json` | `["manual-step"]` | Default manual-step form |

### Tests

- **8 Python backend tests** in `tests/dashboard/hil/test_template_registry.py` — global load, project override wins, global fallback, neither→NotFound, missing with project_repo, `hil_kinds` conflict, null `hil_kinds` allowed, fields replacement
- **15 Python API tests** in `tests/dashboard/api/test_hil_post.py` — HilItemDetail envelope (all three kinds, not-found), template endpoint (found, not-found, project override), POST answer (ask/review/manual→200, 404, idempotent, 409 conflict, 422 wrong kind, disk persistence)
- **34 frontend unit tests** across 6 spec files — AskForm, ReviewForm, ManualStepForm, FormRenderer, TemplateRegistry, HilItem
- **588 Python tests** total (all passing); **111 frontend tests** total (all passing)

## Notable design decisions made during implementation

1. **`HilItemDetail` wrapper, not `HilItem` mutation.** `HilItem` lives in `shared/` and is the wire format for disk + job driver. Adding presentation fields there would leak dashboard concerns into shared. The wrapper lives in `dashboard/api/hil.py` and is invisible to the job driver.
2. **Kind-based `ui_template_name` default (v0).** In v0 there is no per-stage template override mechanism (that requires a `StageRun.ui_template` field, planned for v1). The kind→template map (`ask`→`ask-default-form`, etc.) is the simplest correct thing; it's a single dict at the top of the API module, easy to find and extend.
3. **`TemplateRegistry` is stateless and constructed per-request.** No memoization in v0. Templates are small JSON files; disk reads are fast; per-request construction keeps the cache's lifetyme model simple.
4. **Override `hil_kinds=None` means "inherit base" (not "unconstrained").** A project override should only be customising presentation, not changing which HIL kinds the template applies to. `null` is the natural "I didn't touch this" sentinel; explicit change raises `TemplateKindConflictError`.
5. **Fields dict is replaced wholesale, not merged.** A project override that changes `submit_label` but keeps `extra_help` must copy both. Deep-merge of arbitrary dicts adds hidden complexity with no clear winning semantics; replacement is explicit and auditable.
6. **`POST /api/hil/{id}/answer` validates `answer.kind == item.kind` before calling the contract.** The contract could also validate this, but failing fast at the API layer gives a clear 422 (validation error) rather than a contract-level error that would be harder to map to the right HTTP status.
7. **TestClient used as context manager in all fixtures.** FastAPI lifespan (which populates `app.state.cache`) only fires when the `TestClient` is used as a context manager (`with TestClient(app) as client:`). Using `TestClient(app)` without the context manager silently skips the lifespan, causing `AttributeError: 'State' object has no attribute 'cache'` in every endpoint test.

## Locked for downstream stages

- **`HilItemDetail` schema is stable.** `{item, job_slug, project_slug, ui_template_name}`. Stage 14+ can add fields but must not remove them.
- **`TemplateRegistry.resolve(name, *, project_repo)` signature is stable.** The `project_repo` parameter is `Path | None`; callers pass a resolved `Path` or `None`.
- **`hil_kinds` in template JSON is a locked kernel field.** Project overrides must never change it. The `TemplateKindConflictError` guard enforces this.
- **Template JSON schema is `UiTemplate` (shared/models/presentation.py).** Future template fields must go into `fields: dict[str, Any]` to preserve schema compatibility.
- **`FormRenderer` emits `submit` with the raw answer dict** (the dict that goes into `POST /api/hil/{id}/answer` body). HilItem.vue is the only consumer; downstream code should not re-parse the FormRenderer's emit format.

## Files added/modified (25)

```
dashboard/api/hil.py                                      (modified — 3 new endpoints, HilItemDetail)
dashboard/hil/template_registry.py                        (new)
dashboard/frontend/src/components/forms/AskForm.vue       (new)
dashboard/frontend/src/components/forms/ReviewForm.vue    (new)
dashboard/frontend/src/components/forms/ManualStepForm.vue (new)
dashboard/frontend/src/components/forms/FormRenderer.vue  (new)
dashboard/frontend/src/components/forms/TemplateRegistry.ts (new)
dashboard/frontend/src/views/HilItem.vue                  (modified — full implementation from stub)

hammock/templates/ui-templates/ask-default-form.json      (new)
hammock/templates/ui-templates/spec-review-form.json      (new)
hammock/templates/ui-templates/design-spec-review-form.json (new)
hammock/templates/ui-templates/impl-spec-review-form.json (new)
hammock/templates/ui-templates/impl-plan-spec-review-form.json (new)
hammock/templates/ui-templates/integration-test-review-form.json (new)
hammock/templates/ui-templates/pr-merge-form.json         (new)
hammock/templates/ui-templates/manual-step-default-form.json (new)

tests/dashboard/hil/test_template_registry.py             (new — 8 tests)
tests/dashboard/api/test_hil_post.py                      (new — 15 tests)
tests/dashboard/api/test_hil.py                           (modified — updated for HilItemDetail shape)

dashboard/frontend/tests/unit/forms/AskForm.spec.ts       (new)
dashboard/frontend/tests/unit/forms/ReviewForm.spec.ts    (new)
dashboard/frontend/tests/unit/forms/ManualStepForm.spec.ts (new)
dashboard/frontend/tests/unit/forms/FormRenderer.spec.ts  (new)
dashboard/frontend/tests/unit/forms/TemplateRegistry.spec.ts (new)
dashboard/frontend/tests/unit/views/HilItem.spec.ts       (new)
```

## Acceptance criteria — met

- [x] `GET /api/hil/{id}` returns `HilItemDetail` with correct `job_slug`, `project_slug`, `ui_template_name`
- [x] `GET /api/hil/templates/{name}` resolves global template; applies per-project override when `project_slug` is given
- [x] Per-project override cannot change `hil_kinds` (409 on conflict)
- [x] `POST /api/hil/{id}/answer` transitions item to `"answered"`, persists to disk
- [x] Idempotent re-submit (same answer) → 200; conflicting re-submit → 409
- [x] Wrong `answer.kind` → 422
- [x] `AskForm`, `ReviewForm`, `ManualStepForm` render correctly and produce correct answer dicts
- [x] `FormRenderer` dispatches to correct form component
- [x] `HilItem.vue` fetches detail + template, renders form for awaiting items, shows "already answered" for non-awaiting, surfaces POST errors
- [x] 8 v0 JSON template files declared and valid
- [x] 588 Python tests pass; 111 frontend tests pass; ruff + pyright clean

## Notes for downstream stages

- **Stage 14 (plan-based template selection):** `ui_template_name` is currently derived from item kind only. To support per-stage template overrides, add a `ui_template: str | None` field to `StageDefinition` (or `StageRun`), and in `GET /api/hil/{id}`, check `stage_run.ui_template` first before falling back to `_KIND_DEFAULT_TEMPLATE`. No change to the `TemplateRegistry` is needed.
- **Per-project templates:** Project repos can drop `.hammock/ui-templates/<name>.json` overrides. The `project_slug` query param on `GET /api/hil/templates/{name}` triggers resolution. The project's `repo_path` must be set in `ProjectConfig` for this to work.
- **Adding template fields:** New presentation data should go into `fields: dict[str, Any]`. The frontend reads `template.fields.submit_label`, `template.fields.extra_help`, and `template.fields.context_artifacts`. Any new `fields` key is safe to add without a schema migration.
- **`HilContract` is the write gateway.** `POST /api/hil/{id}/answer` always goes through `HilContract.submit_answer`; the contract handles idempotency and conflict detection. Do not write HIL items directly from the API layer.
