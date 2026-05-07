# Development process

Read this **before** starting work. The discipline here is what keeps the codebase shippable.

## TDD: red → green → refactor

Every behavioural change goes through three phases:

1. **Red** — write the test that captures the new behaviour. Run it. Confirm it fails for the *right reason* (not a typo, not a missing import). The failure mode is the spec.
2. **Green** — write the smallest implementation that turns the test green. Don't refactor yet. Don't add adjacent features.
3. **Refactor** — only after green, and only with the test as your safety net.

Tests are frozen during the green phase. If a test turns out to be wrong, finish the task on the original spec, then file a follow-up to update the test — never edit a test mid-implementation to make it pass.

When in doubt about which layer to test at, see `testing.md` ("when to add a test at which layer").

## Stage-by-stage breakdown

A multi-step feature ships as multiple PRs, one per stage. Each stage:

- Has a single goal stated in one sentence.
- Stands on its own — main is shippable after every stage merges.
- Includes its own red phase (tests asserting the stage's contract) before any implementation.
- Has a fixture sweep, if needed, in the same PR as the contract change. Never spread one contract change across two PRs.

The workflow customization plan in `docs/hammock-workflow.md` is the canonical example: 6 stages, 6 PRs, each independently mergeable.

## One PR per stage

Branch off latest main for each stage. PR title = `stageN: <one-line goal>`. PR body covers:

- Summary of changes.
- Why (link to design doc or issue).
- TDD evidence (red phase confirmed before green).
- Local gauntlet results (per `rules.md`).
- Test plan including any manual verification still pending.

Don't open a PR until the local preflight gauntlet is green.

## Preflight before push (FULL gauntlet)

CI runs more checks than the obvious "ruff + pytest + vitest". Skipping any of them locally means CI catches what you missed and burns a cycle. The full list is in `.github/workflows/*.yml`. As of now:

**Backend (per Python version 3.12 + 3.13):**

```
.venv/bin/ruff format .              # apply formatting first
.venv/bin/ruff check .               # lint LAST (after formatters)
.venv/bin/pyright shared/ dashboard/ # strict; commonly missed
.venv/bin/pytest -q
uv run --python 3.13 --with pytest --with pytest-asyncio --with pytest-timeout \
  --with hypothesis --with httpx --with anyio --with-editable . pytest -q
```

**Frontend (`cd dashboard/frontend`):**

```
pnpm format               # write
pnpm lint                 # eslint, after format
pnpm type-check           # vue-tsc
pnpm test                 # vitest
pnpm build                # vite build; commonly missed
```

**E2E (in dashboard/frontend):**

```
pnpm test:e2e             # playwright; commonly missed
```

**Order matters.** Lint goes LAST, after every formatter pass — see `gotchas.md` "lint-after-format ordering" for the regression that established this rule.

## CI gates

PR runs three jobs from `.github/workflows/`:

- `backend.yml` — ruff check, ruff format --check, pyright strict, pytest on 3.12 + 3.13.
- `frontend.yml` — eslint, prettier --check, vue-tsc, vitest, vite build.
- `e2e.yml` — Playwright against a live dashboard.

The local preflight above mirrors these. Diff against the YAMLs whenever they change.

## Before claiming "done"

Verification before completion is mandatory. Don't write "complete", "fixed", or "all tests pass" before:

1. Running the verification command and reading the output.
2. Confirming the output matches expectations.
3. (For UX or real-claude paths) running the actual flow end-to-end.

Things that look like verification but aren't:

- Type-check passing — proves shapes, not behaviour.
- Build passing — proves it compiles, not that it works.
- Mock-based tests passing — proves the seam, not the integration.
- Lint passing — proves style, not anything else.

For code that calls real Claude, run a smoke job. For UI work, open the page and click. The first real run of any new claude-prompt path will surface 1–2 issues that no fake-runner test catches; budget time for it.

## Memory vs scripts

If you find yourself adding a third "remember to do X" to memory, write X into the codebase as a script or hook instead. Memory is for context; scripts are for checklists. The `bin/preflight.sh` is the right shape; one `make ci` command beats three notes.

## Subagents for sweeps

When delegating a fixture sweep (e.g., "add `schema_version: 1` to every yaml"):

1. **Discovery first.** Ask the agent to *find all sites* and report the count.
2. **Review the count.** Compare against your mental model. If the agent found a file you didn't expect, that's a signal to investigate before the sweep.
3. **Then sweep.** Now the agent does the mechanical edit.

Don't conflate discovery and execution into one prompt — you'll miss sites and not know.

## Editing files programmatically

Use `Edit` and `Write` for file changes. Don't use `sed` for any file containing Python type annotations — `Path | None` and similar break basic regex tools (the `|` is a special char in extended regex). The 30 seconds of `sed` "speed" routinely costs minutes of unwinding malformed substitutions.

## Don't write code for hypothetical futures

A bug fix doesn't need surrounding cleanup. A new feature doesn't need an abstraction layer "in case." Three similar lines is better than a premature abstraction. Backwards-compatibility shims for changes that haven't shipped yet are dead weight.

If a change feels like it needs more than the immediate work, file a follow-up issue. Don't ship the abstraction until the second user shows up.

## Comments

Default to writing none. Only add a comment when the *why* is non-obvious — a hidden constraint, a subtle invariant, a workaround for a specific bug. Don't explain *what*; well-named identifiers do that. Don't reference the current task ("added for issue #123") — that goes in the PR description and rots in the codebase.
