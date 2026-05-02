# Stage 3 - Plan Compiler

**PR:** [#6](https://github.com/NitinJ/hammock/pull/6) (in flight)
**Branch:** `feat/stage-03-plan-compiler`

## What was built

`compile_job(...)` turns a `(project_slug, job_type, title, request_text)` tuple into a validated job directory on disk. Deterministic Python; no LLM calls. Surfaced via `hammock job submit ...`.

- **`dashboard/compiler/compile.py`**. Public entry point. Pipeline:
  1. Resolve project from registry (must exist).
  2. Load global template (bundled or `~/.hammock/job-templates/`) + optional per-project override at `<repo>/.hammock/job-template-overrides/<job_type>.yaml`.
  3. Modify-only deep merge.
  4. Bind `${...}` placeholders against `{job: {id,slug,title,type}, project: {slug,name}}`.
  5. Pydantic-validate every stage via `StageDefinition`.
  6. Run 7 structural validators.
  7. Generate `job_slug` (`YYYY-MM-DD-<title-slug>`, collision suffix `-2`, `-3`, ...).
  8. Atomically write `job.json` + `prompt.md` + `stage-list.yaml`.
  Returns `CompileSuccess(job_slug, job_dir, JobConfig, stages, dry_run)` or `list[CompileFailure]`.
- **`dashboard/compiler/overrides.py`**. Modify-only merge by stage id. Rejects add/remove/reorder/unknown-id with structured `OverrideFailure`. Lists are replaced wholesale (no list-merging).
- **`dashboard/compiler/validators.py`**. Seven rules:
  1. `unique_ids` - no duplicate stage ids
  2. `dag_closure` - required inputs come from prior stages or `JOB_LEVEL_INPUTS={prompt.md}`
  3. `loop_back_targets` - must reference earlier stage, not self
  4. `parallel_with` - symmetric + references existing ids
  5. `predicates` - `runs_if` + `loop_back.condition` parse against the grammar
  6. `human-presentation` - `worker:human` stages need `presentation` block
  7. `no_path_traversal` - paths relative + no `..`
- **`hammock/templates/job-templates/build-feature.yaml`**. 12 stages across 7 phases per design doc § Job template format. Each spec phase uses WRITE -> AGENT_REVIEW -> HIL_REVIEW with `loop_back` to WRITE on rejection.
- **`hammock/templates/job-templates/fix-bug.yaml`**. Same shape; `bug-report.md` replaces `problem-spec.md`; `write-bug-report` replaces `write-problem-spec`.
- **`cli/job.py`**. `hammock job submit --project --type --title (--request-text|--request-file) [--dry-run] [--json]`. Wraps `compile_job`. Pretty + JSON output paths. Mounted on the main app.

## Notable design decisions

1. **Templates ship under `hammock/templates/job-templates/`** in the package, not `~/.hammock/job-templates/`. The compiler resolves user dir first, falls back to bundled. Stage 4+ may add a "first-run copy" step; v0 reads bundled directly. This avoids a chicken-and-egg between the CLI and the user's `~/.hammock/`.
2. **`JOB_LEVEL_INPUTS={prompt.md}`** is the only universal job-level input. Optional inputs are unchecked - the agent handles missing-optional gracefully. This is why templates can list things like `requirements.md`, `prior-art.md`, `logs.md` as optional without DAG-closure errors.
3. **Slug generation is `YYYY-MM-DD-<derive_slug(title)>` + collision suffix.** Design doc mentions LLM-summarised slugs as a graceful-fallback feature; v0 uses `derive_slug(title)` directly. Cheap, deterministic, no API calls.
4. **All validation errors aggregated** before returning. Compile aborts as a unit; users see every violation in one pass instead of fixing them one at a time.
5. **`override` is typed `Any`** in the merge function signature because `yaml.safe_load` returns `Any`. Runtime `isinstance` checks validate the structure. Honest about the trust boundary.
6. **`list_replaced_wholesale` for override merge.** The design doc explicitly rules out list-merging because the semantics are ambiguous (replace? append? interleave by index?). Override an entire list with the override's value, full stop.
7. **`compile_job` has a `now` arg** for deterministic tests. Production passes `None` (defaults to `datetime.now(UTC)`); tests pass a fixed datetime so the slug derivation is reproducible.
8. **Pretty + JSON output split.** `--json` uses stdlib `json.dumps`; pretty path uses Rich tables. Same pattern as Stage 2.

## Locked for downstream stages

- **`compile_job(...)` signature is the contract.** Stage 4's job-driver-spawn flow calls it. Stage 9's HTTP `POST /api/jobs` will call it. Don't change signature without a structural-change stage.
- **`CompileSuccess` and `CompileFailure` are the canonical result shapes.** Add fields, don't rename existing ones.
- **`JOB_LEVEL_INPUTS` defaults to `{prompt.md}`.** Future templates may need additional job-level inputs (e.g., `requirements.md` as a real required input). Adding to the set is a structural change; do it in a dedicated stage.
- **The two v0 templates are kernel-stable.** Per-project overrides may modify them via the override path. The bundled YAML is structurally locked - changing topology would break compatibility with project-shipped overrides.

## Files added/modified

```
dashboard/compiler/__init__.py
dashboard/compiler/compile.py
dashboard/compiler/overrides.py
dashboard/compiler/validators.py

hammock/templates/job-templates/build-feature.yaml
hammock/templates/job-templates/fix-bug.yaml

cli/job.py
cli/__main__.py                                 (mount job_app)

tests/dashboard/compiler/__init__.py
tests/dashboard/compiler/conftest.py
tests/dashboard/compiler/test_compile.py        (16 tests)
tests/dashboard/compiler/test_validators.py     (20 tests)
tests/cli/test_job_submit.py                    (6 tests)

scripts/manual-smoke-stage3.py

pyproject.toml                                  (+pyyaml>=6)
uv.lock                                         (regenerated)

docs/stages/stage-03.md                         (this file)
docs/stages/README.md                           (index updated)
```

## Dependencies introduced

| Layer | Package | Version | Purpose |
|---|---|---|---|
| runtime | `pyyaml` | `>=6` | Parse job templates + override files |

## Acceptance criteria - met

- [x] Both v0 templates compile cleanly against an empty-overrides project
- [x] Override merge is modify-only; add/remove/reorder/unknown-id rejected with clear messages
- [x] `runs_if` and `loop_back.condition` predicates parse against the grammar (Stage 0 evaluator)
- [x] All writes atomic via `shared.atomic`; failed compile leaves nothing partial
- [x] `hammock job submit ... --dry-run` returns the would-be plan without writing
- [x] CI green on matrix py3.12 + py3.13

## Notes for downstream stages

- **Stage 4 (Job Driver state machine)**: spawn the driver after `compile_job` returns success. The compiler does NOT spawn anything - it writes the job dir and returns. The Job Driver is a separate subprocess that attaches to the job dir. Wire `dashboard/driver/lifecycle.py:spawn_driver(job_slug)` to fire after a successful HTTP `POST /api/jobs` (which goes through `compile_job` first).
- **Stage 4 also owns the git seed.** The design doc § Compilation algorithm step 7 ("Seed git workspace - create `job/<slug>` branch off main") is deferred to Stage 4 because it touches the project repo, not the hammock root. Stage 3 stays purely in `~/.hammock/`.
- **Stage 4 establishes the events.jsonl writer pattern.** When that lands, emit `job_submitted` from the compiler-success path (or have the Job Driver supervisor emit it). Currently Stage 3 emits no events.
- **Stage 9 (HTTP API)**: `POST /api/jobs` calls `compile_job` directly. On `CompileSuccess` return 201 with the job slug; on `list[CompileFailure]` return 422 with the structured failures array. The CLI `--json` output shape is the same wire format.
- **Stage 14 (Job submit UI)**: dry-run path is the natural fit for the form's "preview plan" button. Reuses the same `--json` output.
- **Slug summarisation (LLM)**: when implemented, it goes in `_generate_job_slug` in `compile.py`. Wrap the LLM call in a try/except, fall back to the current `derive_slug(title)` path on any failure. The current implementation is the fallback.
- **Doctor light-check before submit (per Stage 2 §)**: Stage 3's CLI does NOT call `cli.doctor.run_light(project)` before submit. The current behaviour assumes the project is registered (which the compiler verifies). Adding the light check would catch missing remotes / bad gh auth before compile-time work. Worth adding when the dashboard process boots; for v0 it's optional.

## Process notes

- **TDD trail in PR**: feat commit first, then test commit. Squash-merged into one. Reviewer can see the impl-then-test order honestly.
- **Per user request, the stage summary lives in this PR** rather than as a post-merge commit (deviates from §8.6 default but explicit user opt-in for this PR).
- **`now=` arg in `compile_job`** lets the test suite hold time fixed. Patterns to copy: any function that consults `datetime.now()` should accept an injected `now` arg for deterministic tests.
