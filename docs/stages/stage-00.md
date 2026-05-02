# Stage 0 — Scaffold + shared models

**PR:** [#1](https://github.com/NitinJ/hammock/pull/1) (merged 2026-05-02)
**Branch:** `feat/stage-00-scaffold`
**Commit on `main`:** `110f79b` (squash-merged as `fa7b491`)

## What was built

The pure-data foundation that every downstream stage imports from. No business logic, no network, no CLI — just the contract surface.

- **Repo plumbing.** `pyproject.toml` (uv-managed) pinning the v0 backend stack; `.python-version=3.12`; `.gitignore`; `.pre-commit-config.yaml` running ruff; `README.md`.
- **Docs.** `docs/design.md` and `docs/implementation.md` are the canonical references (renamed from the dated/long-form filenames). Three pre-consolidation drafts kept under `docs/` as supplementary context: `02-proposal-lifecycle.md`, `hil-bridge-mcp-section.md`, `presentation-plane.md`.
- **`shared/` helpers (pure functions).**
  - `paths.py` — every path under `~/.hammock/` exposed as a function. Default root is `~/.hammock`; overridable via `HAMMOCK_ROOT` env var or per-call `root=` arg.
  - `atomic.py` — `atomic_write_text` / `atomic_write_json` / `atomic_append_jsonl` with fsync. Append rejects lines >4000 bytes (POSIX `PIPE_BUF` safety).
  - `slug.py` — kebab-case `[a-z0-9-]+`, max 32 chars; derivation per § Project Registry; raises `SlugDerivationError` when basename is all non-alphanumerics.
  - `predicate.py` — full parser + evaluator for the locked minimal grammar (dotted-path access, `==`/`!=`, string + bool literals, `and`/`or`/`not`). No arithmetic, no function calls. Stage 3 wires this into the Plan Compiler.
- **`shared/models/` — 10 files.** Every Pydantic model named in impl plan §5.3 transcribed faithfully:
  - `project.py` — `ProjectConfig`/`Project`
  - `job.py` — `JobConfig`, `JobState`, `JobCostSummary`, `StageCostSummary`, `AgentCostSummary`
  - `stage.py` — `StageDefinition`, `StageRun`, `StageState`, `Budget`, `ExitCondition`, `LoopBack`, `OnExhaustion`, `InputSpec`, `OutputSpec`, `RequiredOutput`, `ArtifactValidator`
  - `task.py` — `TaskRecord`, `TaskState`
  - `hil.py` — `HilItem`, `AskQuestion`/`Answer`, `ReviewQuestion`/`Answer`, `ManualStepQuestion`/`Answer` (discriminated unions on `kind`)
  - `events.py` — `Event` envelope + `EVENT_TYPES` taxonomy frozenset
  - `plan.py` — `Plan`, `PlanStage` (alias of `StageDefinition` in v0)
  - `presentation.py` — `PresentationBlock`, `UiTemplate`
  - `specialist.py` — `AgentDef`, `SkillDef`, `AgentEntry`, `SkillEntry`, `SpecialistCatalogue`, `MaterialisedSpawn`
  - `verdict.py` — `ReviewVerdict`, `ReviewConcern`
- **Tests.** 105 tests under `tests/shared/`: factory + roundtrip + at-least-one rejection per model; Hypothesis property tests for slug derivation and path canonicalisation; full predicate parser/evaluator coverage.
- **Smoke.** `scripts/manual-smoke-stage0.py` instantiates one of every model, prints JSON, asserts round-trip — 15 model variants.
- **CI.** `.github/workflows/backend.yml` matrix Python 3.12 + 3.13, running ruff check + format check + pyright (strict on `shared/`) + pytest. ~19s per matrix job.

## Notable design decisions made during implementation

These weren't pinned in `design.md` or `implementation.md`; we resolved them here.

1. **`HilItem` cross-validator.** Pydantic's discriminated union routes `question` to the right concrete type based on its own `kind`, but the *outer* `HilItem.kind` is a parallel field. We added `@model_validator(mode="after")` ensuring the three `kind`s (outer, question's, answer's) all match. Without this, `HilItem(kind="ask", question=ReviewQuestion(...))` would validate.
2. **Enums use `StrEnum` with explicit values.** `class JobState(StrEnum): SUBMITTED = "SUBMITTED"`. Default `StrEnum` would lowercase the value (`"submitted"`); explicit values keep the design-doc strings verbatim.
3. **`paths.py` accepts an explicit `root=` arg.** Tests pass `tmp_path` directly; production reads the module-level `HAMMOCK_ROOT`. No monkey-patching needed.
4. **`atomic_append_jsonl` enforces `PIPE_BUF` safety at write time.** Lines >4000 bytes raise `ValueError` rather than risk torn appends. Forces large payloads to side files (consistent with the design's single-writer-per-file discipline).
5. **`PlanStage = StageDefinition`.** §5.3 names both; in v0 they're the same shape (compile-time vs run-time view of one class). If divergence ever appears, split then.
6. **Three pre-consolidation drafts kept in `docs/`.** They're the staging files referenced in the design doc's iteration log; the master `design.md` integrates their content but the originals retain useful framing. Not load-bearing.

## Locked for downstream stages

- `shared/paths.py` is the *only* place hardcoded paths may live. Stage 8 will add an import-linter rule that enforces this.
- `shared/atomic.*` is the only sanctioned write pathway for files in the hammock root. The single-writer-per-file map in `design.md` rests on every writer using these.
- `shared/models/__init__.py` re-exports the canonical surface. Adding a field to an existing model is a structural change requiring its own stage. New models are additive and OK.
- `Event.payload` is intentionally `dict[str, Any]`. Per-event-type payload models will land alongside the consumers that need them (cache, cost rollup, Soul). Producers and consumers share the `EVENT_TYPES` frozenset.

## Files added (35)

```
.github/workflows/backend.yml
.gitignore (modified)
.pre-commit-config.yaml
.python-version
README.md
docs/02-proposal-lifecycle.md         (renamed-in)
docs/design.md                         (renamed from 2026-05-02-hammock-design.md)
docs/hil-bridge-mcp-section.md        (renamed-in)
docs/implementation.md                (renamed from hammock-implementation.md)
docs/presentation-plane.md            (renamed-in)
pyproject.toml
scripts/manual-smoke-stage0.py
shared/__init__.py
shared/atomic.py
shared/models/__init__.py
shared/models/{project,job,stage,task,hil,events,plan,presentation,specialist,verdict}.py
shared/paths.py
shared/predicate.py
shared/slug.py
tests/__init__.py
tests/conftest.py
tests/shared/__init__.py
tests/shared/factories.py
tests/shared/test_atomic.py
tests/shared/test_models_*.py         (10 files)
tests/shared/test_paths.py
tests/shared/test_predicate.py
tests/shared/test_slug.py
uv.lock
```

## Dependencies introduced

| Layer | Package | Version |
|---|---|---|
| runtime | `pydantic` | `>=2.6,<3` |
| dev | `pytest` | `>=8` |
| dev | `pytest-asyncio` | `>=0.23` |
| dev | `pytest-cov` | `>=5` |
| dev | `hypothesis` | `>=6` |
| dev | `factory-boy` | `>=3.3` |
| dev | `pyright` | `>=1.1.350` |
| dev | `ruff` | `>=0.4` |
| dev | `pre-commit` | `>=3.7` |

(Resolved versions live in `uv.lock`.)

## Acceptance criteria — met

- [x] All Pydantic models in design doc § HIL bridge, § Observability, § Stage as universal primitive, § Project Registry, § Plan Compiler, § Lifecycle exist and validate.
- [x] `from shared.paths import job_dir; job_dir("foo")` returns `~/.hammock/jobs/foo`.
- [x] `atomic_write_json(path, model)` produces a file containing `model.model_dump_json()`.
- [x] 100% type-checked under pyright strict on `shared/`; no `# type: ignore`.
- [x] CI passes on the PR (matrix py3.12 + py3.13 both green).

## Notes for downstream stages

- **Stage 1 (cache + watchfiles)** can rely on every Pydantic model being constructible from the JSON its own writer produces — `model_validate_json(model.model_dump_json())` round-trips. The cache's parse layer is therefore symmetric.
- **Stage 2 (Project Registry CLI)** should reuse `derive_slug`, `validate_slug`, and the path helpers — don't re-derive paths. CLI tests should construct Pydantic models via the existing factories in `tests/shared/factories.py` (move to `tests/conftest.py` or a `tests/factories/` package once a second stage uses them).
- **Stage 3 (Plan Compiler)** wires `shared.predicate.parse_predicate` / `evaluate_predicate` for `runs_if` and `loop_back.condition`. Validators should `parse_predicate(...)` at compile time to fail fast.
- **Stage 4 (Job Driver)** writes `events.jsonl`; producers must keep payloads under 4000 bytes/line per `atomic_append_jsonl`'s guarantee. Larger blobs go to side files.
- **All future stages**: adding a Pydantic field is a structural change. New stage required.
