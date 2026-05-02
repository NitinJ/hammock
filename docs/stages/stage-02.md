# Stage 2 — Project Registry CLI

**PR:** [#3](https://github.com/NitinJ/hammock/pull/3) (merged 2026-05-02)
**Branch:** `feat/stage-02-project-registry-cli`
**Commit on `main`:** `b959362` + `82a872e` (squash-merged as `f5aad01`)

## What was built

The `hammock project ...` CLI surface — seven verbs that own the registry, override skeleton, and per-project doctor. This is the gateway to the entire control-plane chain (Stages 3 → 4 → 5 → 6 → 7 layer on top).

- **`cli/__main__.py`** — typer app entry; mounts `project_app`. Console script `hammock` wired in `pyproject.toml [project.scripts]`.
- **`cli/project.py`** — seven verbs: `register`, `list`, `show`, `doctor`, `relocate`, `rename`, `deregister`. Each is a typer `@project_app.command` with `Annotated`-typed args + `--json` flag where applicable.
- **`cli/doctor.py`** — `DoctorReport` + `CheckResult`; `run_full(project)` (12 checks) and `run_light(project)` (4-check pre-job subset). Auto-fix on warn-level drift (override-skeleton recreation, gitignore append) — idempotent. `write_back` updates `last_health_check_*` on `project.json`.
- **`cli/_external.py`** — thin git/gh subprocess wrappers (`git_remote_url`, `git_default_branch`, `git_working_tree_dirty`, `git_is_repo`, `gh_auth_ok`, `gh_repo_view`). Single seam; tests `monkeypatch.setattr` these.
- **`shared/models/project.py`** — additive: `last_health_check_at: datetime | None` + `last_health_check_status: Literal["pass","warn","fail"] | None`. Both default `None`. Backfills the design-doc spec; missed during Stage 0 transcription.
- **`tests/cli/`** — 38 tests across `test_project_register.py`, `test_project_other.py`, `test_doctor.py`. `conftest.py` defines `hammock_env` (HAMMOCK_ROOT monkeypatch), `fake_repo`, `patch_external` (single-seam git/gh mock), and `normalize()` (ANSI-stripping helper for substring assertions).
- **`scripts/manual-smoke-stage2.py`** — end-to-end against `uv run hammock` in real subprocesses. Drives all seven verbs against a temp `HAMMOCK_ROOT` and a fake repo. `--skip-remote-checks` so it works offline / without gh-auth.

## Notable design decisions made during implementation

1. **Two optional fields added to `ProjectConfig`** (`last_health_check_at`, `last_health_check_status`). The design doc § Project Registry explicitly specs these in the Pydantic schema; Stage 0's manifest didn't capture them. Adding them now is fully backward-compatible (optional, default `None`) — every existing serialised `project.json` from before Stage 2 still validates. Strict-Stage-0-immutability would have meant carrying state outside the model, which the design opposes ("doctor writes back to project.json").
2. **`cli/_external.py` is the only git/gh seam.** Any subprocess call to `git`/`gh` lives there. Tests `monkeypatch.setattr(_external, "fn", lambda ...: stub)` and stay offline. The `patch_external` fixture provides ok-by-default returns; tests override per-key for error paths.
3. **JSON output uses stdlib `json.dumps`, not `Console.print_json`.** Rich auto-wraps + ANSI-colors JSON; `--json` mode needs parseable output for scripting. `typer.echo(json.dumps(data, indent=2, default=str))` is the pattern; `default=str` handles `datetime` and `Path`.
4. **Rich console with `highlight=False`.** Auto-highlighting splits identifiers like `"myrepo-2026"` across color spans (digits cyan, separator dim), breaking substring assertions. Explicit `[red]...[/red]` markup still works.
5. **All seven verbs shipped** (register / list / show / doctor / relocate / rename / deregister). The impl plan §7 listed six (missing `relocate`, said `info` instead of `show`); the design doc § Project Registry is the authoritative spec for the verb set. Following design.
6. **Doctor checks #10 (orphan worktrees) and #12 (Job Driver liveness) are stubs.** Both depend on Stage 4+ infrastructure. Reported as `info` severity until then. The slot in the report is fixed (12 checks always); only the assertion they make is partial.
7. **No watchfiles wiring for skill-override mirroring** (`~/.claude/skills/<slug>__<id>` symlinks). Requires the dashboard process. Stage 8 boots that; the skill-override mirror lands when watcher gains its initial subscriber set. Stage 2 just creates the empty `<repo>/.hammock/skill-overrides/` directory.
8. **No `events.jsonl` "project_registered" emission.** Stage 4 establishes the event-writer pattern; doing it here would mean a half-written event-log scheme.
9. **Smoke script forces `--default-branch main` + `--skip-remote-checks`.** Fake repos have no commits → `git symbolic-ref refs/remotes/origin/HEAD` fails; no real GitHub repo → `gh repo view` fails. These flags are designed for offline/scripted use anyway.
10. **`conftest.normalize(output)`** strips ANSI escapes and collapses whitespace. Rich wraps long lines based on terminal width and re-emits color codes per wrapped line — a long red error message can be split across `\n` boundaries, breaking naive `substring in res.output` assertions.

## Locked for downstream stages

- **`cli/_external.py` is the contract for all `git`/`gh` access** in the CLI. Stages 3+ MUST add new wrappers here, not call `subprocess` directly elsewhere.
- **`cli.doctor.run_light(project) -> DoctorReport`** is the canonical pre-job health gate. Stage 3's `hammock job submit` will call it before invoking the compiler.
- **`cli.doctor.run_full(project, *, auto_fix=True, root=...)`** is what the future dashboard project-detail view will invoke (Stage 9+).
- **`ProjectConfig.last_health_check_*`** semantics: `pass`/`warn` allow job submission; `fail` blocks. The dashboard's job-submit endpoint will read these.
- **`<repo>/.hammock/` skeleton is exactly five subdirs**: `agent-overrides/`, `skill-overrides/`, `hook-overrides/quality/`, `job-template-overrides/`, `observatory/`. Plus a `README.md`. Anything else is non-canonical.
- **The seven verbs are the public CLI surface for project management.** Stage 16's dogfood test will use them. Don't break their signatures without a structural-change stage.

## Files added/modified (15)

```
cli/__init__.py
cli/__main__.py
cli/_external.py
cli/doctor.py
cli/project.py

tests/cli/__init__.py
tests/cli/conftest.py
tests/cli/test_doctor.py
tests/cli/test_project_other.py
tests/cli/test_project_register.py

scripts/manual-smoke-stage2.py

shared/models/project.py        (+last_health_check_at, +last_health_check_status)
pyproject.toml                  (+typer>=0.12, +rich>=13, +cli/ in packages, +hammock console script)
uv.lock                         (regenerated)

docs/stages/stage-02.md         (this file — added post-merge per §8.6)
docs/stages/README.md           (index updated)
```

## Dependencies introduced

| Layer | Package | Version | Purpose |
|---|---|---|---|
| runtime | `typer` | `0.25.1` | CLI framework |
| runtime | `rich` | `15.0.0` | Terminal output (also pulled by typer) |
| transitive | `click` | `8.3.3` | typer's foundation |
| transitive | `markdown-it-py`, `mdurl`, `pygments`, `shellingham`, `annotated-doc` | various | rich + typer indirect deps |

## Acceptance criteria — met

- [x] `hammock project register /path/to/repo` produces `~/.hammock/projects/<slug>/project.json` and `<path>/.hammock/{agent-overrides,skill-overrides,hook-overrides/quality,job-template-overrides,observatory}/`.
- [x] Slug derivation matches design doc worked example: `figur-Backend_v2 → figur-backend-v2` (covered in `tests/shared/test_slug.py` from Stage 0).
- [x] Doctor full <2s; light <200ms (asserted in `test_run_full_completes_under_2s` / `test_run_light_completes_under_200ms`).
- [x] Deregister surfaces consequences-preview before destructive action (`test_deregister_declined_at_prompt`).
- [x] All commands have `--help` (typer-generated).
- [x] CI green on matrix py3.12 + py3.13 (twice — once before Stage 8 merged, once after rebase).

## Notes for downstream stages

- **Stage 3 (Plan Compiler)** — `hammock job submit` should call `cli.doctor.run_light(project)` before invoking the compiler. If it returns `status == "fail"`, abort with the failed checks. The compiler itself is in `dashboard/compiler/` per impl plan §4.
- **Stage 4 (Job Driver)** — fill in doctor checks #10 (orphan worktrees) and #12 (Job Driver liveness). Both already have stub implementations returning `info`; replace with real logic. Also: hook the "emit `project_registered` event" step from the design's init checklist into your event-writer.
- **Stage 8 (FastAPI shell)** — once you boot the dashboard process, register `<repo>/.hammock/skill-overrides/` to the watchfiles set per design doc § Project Registry § Lazy override resolution. ~30 lines of glue: subdir created → `~/.claude/skills/<slug>__<id>` symlink in; subdir removed → drop the symlink.
- **Stage 9 (HTTP API)** — the project-list endpoint can call `cli.project._list_projects(root)` directly; it's pure (just reads `project.json` files under `~/.hammock/projects/`). Reuse don't reinvent.
- **Stage 13+ (forms + dashboard)** — when you add UI for project management, the doctor JSON output (`hammock project doctor <slug> --json`) is the canonical machine-readable shape. Don't re-derive.

## Process / methodology notes

- **TDD discipline trail visible in PR diff** — `feat: impl` first, then `test: coverage`. Squash-merged into one commit on `main`. Honest about the order: the impl was written before tests in this stage; future stages should follow stricter tests-first order per §8.5.
- **Rebase resolution worked exactly as §8.4 prescribes.** Stage 8 merged first; Stage 2 PR showed `pyproject.toml` + `uv.lock` conflicts. Resolution: `git fetch origin; git rebase origin/main`, dedupe `rich>=13` in pyproject (both stages added it; reorder alphabetically), `uv sync --dev` to regenerate `uv.lock`, verify green, `git push --force-with-lease`. CI re-ran green. Total wall-clock: ~3 minutes. Zero human involvement beyond "go".
- **The `patch_external` fixture pattern** (single-seam mock dict in conftest) generalises. Stage 4's tests will need an equivalent for subprocess.Popen of CLI sessions — same shape, different module.
