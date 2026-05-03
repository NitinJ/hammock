# Hammock runbook

How to install, configure, and operate a hammock instance. Companion to
`design.md` (canonical architecture) and `implementation.md` (stage plan).
This document is the operator-facing reference; if you have never run
hammock before, start here.

---

## 1. Install

### Prerequisites

- macOS or Linux (Windows untested in v0).
- Python ≥ 3.12.
- [`uv`](https://docs.astral.sh/uv/) for Python dependency and venv
  management. (Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`.)
- `git` ≥ 2.40.
- [`gh`](https://cli.github.com/) ≥ 2.40 — required by `hammock project
  register` for remote-reachability checks (skippable with
  `--skip-remote-checks` when working purely locally).
- `pnpm` ≥ 9 — only needed if you want to rebuild the dashboard frontend
  bundle from source.
- A working `claude` CLI in `$PATH` if you want to run real (non-fake) jobs.

### Get the code and install dependencies

```bash
git clone https://github.com/NitinJ/hammock.git
cd hammock
uv sync --dev
```

`uv sync` creates `.venv/`, installs everything pinned in `uv.lock`, and
exposes the `hammock` console script under `uv run hammock ...`. To use it
without `uv run`, activate the venv (`source .venv/bin/activate`).

### Verify the install

```bash
uv run hammock --help            # CLI help screen
uv run hammock project list      # empty table on first run
uv run pytest tests/ -q          # full backend suite (≤ 30 s)
```

---

## 2. First run

Hammock has two long-lived components: the **dashboard** (FastAPI + Vue
SPA, one process) and **job drivers** (one process per active job, spawned
by the dashboard via double-fork; they outlive the dashboard).

### Choose a hammock root

Every project, job, artifact, event log, HIL item, and PID file lives
under one root directory. Default is `~/.hammock/`. Override with
`HAMMOCK_ROOT=/path/to/root`.

```bash
export HAMMOCK_ROOT="$HOME/.hammock"
```

The directory is created on demand the first time you register a project.

### Start the dashboard

```bash
uv run python -m dashboard
```

Defaults: `HAMMOCK_HOST=127.0.0.1`, `HAMMOCK_PORT=8765`.

Open <http://127.0.0.1:8765/> in a browser. Empty queue, empty job list —
that's expected on a fresh root.

The dashboard process runs forever (no auto-shutdown). Use a process
manager (`launchd`, `systemd --user`, `tmux`, `screen`) if you want it to
survive reboots.

---

## 3. Register a project

The "project registry" is hammock's record of which repos it can target.
A project is a path on disk + a remote URL + a slug. Slugs are immutable.

```bash
uv run hammock project register /path/to/repo
```

What `register` does (idempotent — re-running on an already-registered
path is a no-op):

1. Verifies the path is a git working tree with an `origin` remote.
2. Verifies `gh auth` succeeds and the remote is reachable (skip with
   `--skip-remote-checks` for local-only work).
3. Detects the default branch (override with `--default-branch`).
4. Derives a slug from the basename (override with `--slug`).
5. Writes `$HAMMOCK_ROOT/projects/<slug>/project.json`.
6. Symlinks `$HAMMOCK_ROOT/projects/<slug>/project_repo` → repo path.
7. Creates the per-project override skeleton at `<repo>/.hammock/`
   (gitignored automatically).
8. Runs the doctor health check with auto-fix.

Then verify:

```bash
uv run hammock project list
uv run hammock project show <slug>
uv run hammock project doctor <slug> --yes   # re-run anytime
```

The other registry verbs (`relocate`, `rename`, `deregister`) are listed
in `hammock project --help`.

---

## 4. Submit a job

A job is one human request + the compiled stage list that fulfils it.
Submit via CLI or via the dashboard's `POST /api/jobs`.

```bash
uv run hammock job submit \
    --project <project-slug> \
    --type fix-bug \
    --title "<short title>" \
    --request-text "<paragraph describing the goal>"
```

Or, for longer prompts:

```bash
uv run hammock job submit \
    --project <project-slug> \
    --type fix-bug \
    --title "<short title>" \
    --request-file path/to/prompt.md
```

`hammock job submit` runs the Plan Compiler synchronously. On success it
prints the new `job_slug`. On failure it prints structured compile errors
and exits non-zero — nothing is written.

`--dry-run` returns the would-be plan without writing the job dir or
spawning a driver. Useful for validating a template change before
committing to a real run.

After submit, the dashboard spawns the Job Driver as a fully detached
subprocess. Watch the job either via `GET /api/jobs/<slug>` or, more
usefully, in the dashboard UI under
<http://127.0.0.1:8765/jobs/`<slug>`>.

The bundled job templates ship in `hammock/templates/job-templates/`.
v0 ships `fix-bug` and `build-feature`; the other four (`refactor`,
`migration`, `chore`, `research-spike`) are deferred to v1+.

---

## 5. Watching a job — the stage live view

The Stage 15 live view is the primary operator UI. URL pattern:

```
http://127.0.0.1:8765/jobs/<slug>/stages/<stage-id>
```

It's a three-pane layout:

- **Left** — task list with live state badges.
- **Centre** — Agent0's prose, tool calls, engine nudges, sub-agent
  regions, and human nudges, scrolling in real-time over Server-Sent
  Events (SSE). Filters: hide tool calls, hide engine nudges, prose-only.
- **Right** — cost vs. budget, stage metadata.

Three actions are always available on a non-terminal stage:

- **Cancel** (`POST /api/jobs/<slug>/stages/<sid>/cancel`) — writes a
  cancel command file the driver picks up within ~2 s. Stage transitions
  to `CANCELLED`.
- **Restart** (`POST /api/jobs/<slug>/stages/<sid>/restart`) — re-spawns
  the driver. Returns 409 if the existing driver process is still alive
  or the per-stage restart cap (3) is exhausted.
- **Chat / nudge** (`POST /api/jobs/<slug>/stages/<sid>/chat`) — appends
  a freeform message to `nudges.jsonl`; Agent0 picks it up at the next
  turn boundary.

All three return 409 if the stage has already reached a terminal state
(`SUCCEEDED`, `FAILED`, `CANCELLED`).

---

## 6. Answering HIL questions

The HIL queue lives at <http://127.0.0.1:8765/hil>. Filter by status
(`awaiting`, `answered`, `cancelled`), kind (`ask`, `review`,
`manual-step`), project, or job.

Click an item to open the form view. The form is rendered from the
stage's `presentation.ui_template` (resolved per-project-first if the
project ships an override under `<repo>/.hammock/ui-templates/`).

Submit answers via the form button or directly via:

```
POST /api/hil/<item-id>/answer
```

Idempotent: re-submitting the identical answer is a no-op; submitting a
*different* answer to an already-answered item returns 409.

> **v1+ note.** The closed-loop "human submits answer → form pipeline
> writes the stage's required output artifact → driver auto-resumes" wire
> is deferred. Today, after submitting a HIL answer for a `worker: human`
> stage you must also (a) ensure the stage's required output artifact is
> on disk, then (b) restart the stage from the live view. The Stage 16
> e2e test shows the on-disk shape required.

---

## 7. Common operations

### List active jobs

```bash
curl -s http://127.0.0.1:8765/api/jobs | jq
uv run hammock job list                    # CLI equivalent (Stage 12+)
```

### Cancel a job

There is no `cancel-job` verb in v0. Cancel each non-terminal stage
individually via the live view's **Cancel** button or `POST
/api/jobs/<slug>/stages/<sid>/cancel`. The driver cleans up.

### Inspect a job's on-disk state

Every job lives at `$HAMMOCK_ROOT/jobs/<slug>/`. Useful files:

- `job.json` — the `JobConfig`; `state` is the headline transition.
- `prompt.md` — the original human request.
- `stage-list.yaml` — the compiled stage plan.
- `stages/<stage-id>/stage.json` — per-stage state (PENDING, RUNNING,
  BLOCKED_ON_HUMAN, SUCCEEDED, FAILED, CANCELLED).
- `events.jsonl` — append-only event log (cost accruals, state
  transitions, agent stream).
- `<artifact>` — every stage output (e.g. `bug-report.md`,
  `design-spec.md`, `summary.md`) lands at the job-dir root unless the
  template puts it elsewhere.
- `job-driver.pid` — the active grandchild PID.
- `heartbeat` — touched every 30 s by the running driver.

### Re-run the project doctor

```bash
uv run hammock project doctor <slug> --yes
```

Auto-fix mode patches what it can (gitignore line, missing `.hammock/`
skeleton, broken symlinks). Without `--yes` it prints the report and
exits 1 if any check failed.

### Generate the OpenAPI schema

```bash
uv run python -c "from dashboard.app import create_app; \
    from dashboard.settings import Settings; \
    import json; \
    print(json.dumps(create_app(Settings()).openapi(), indent=2))" > openapi.json
```

---

## 8. Troubleshooting

### Driver doesn't progress past `SUBMITTED`

Most common cause: the dashboard spawned the driver but the driver
exited immediately. Check `logs/job-driver-<slug>.log` (when present)
and the dashboard process stderr.

If you spawned via `python -m job_driver` directly (not via the
dashboard), the driver requires `--fake-fixtures <dir>` in v0 (Stage 5
has not yet shipped a real `claude`-spawning runner). Without it the
driver exits with code 2.

To run a job with the real Claude path you must arrange for the
dashboard to spawn the driver and the driver's runner selection to use
`RealStageRunner`. Until that runner ships (v0+ Stage 5), use fake
fixtures via `Settings.fake_fixtures_dir` (env var
`HAMMOCK_FAKE_FIXTURES_DIR`).

### Stage stuck on `BLOCKED_ON_HUMAN` after answering

The HIL → artifact → driver-resume bridge is not closed in v0. After
answering, manually:

1. Confirm the stage's required output artifact exists in
   `$HAMMOCK_ROOT/jobs/<slug>/<artifact>` (and is valid against its
   schema, e.g. `review-verdict-schema`).
2. Edit `$HAMMOCK_ROOT/jobs/<slug>/stages/<sid>/stage.json`: set
   `state` to `SUCCEEDED` and `ended_at` to the current ISO-8601 UTC
   timestamp.
3. Click **Restart** in the live view (or `POST .../restart`) to
   re-spawn the driver.

### Heartbeat stale (no progress + last-update timer climbs past ~90 s)

The supervisor flags drivers as stale at 3× the heartbeat interval
(default 90 s). If the dashboard reports a stale driver:

1. Check the PID file at `$HAMMOCK_ROOT/jobs/<slug>/job-driver.pid`.
2. `ps -p <pid>` — if dead, restart the stage from the live view.
3. If alive but unresponsive, send `SIGTERM` (`kill <pid>`); the driver
   has a `SIGTERM` handler that flips the stage to `CANCELLED` and the
   job to `ABANDONED`. Then restart the stage.

### Compile failure on `hammock job submit`

Each failure has a `kind`, `stage_id`, and `message`. Common kinds:

- `project_not_found` — slug typo or project never registered.
- `template_not_found` — job-type typo, or the per-project override
  shadows a template that doesn't exist.
- `validator_error` — a stage's artifact-validator schema is missing or
  the schema check fails.
- `predicate_error` — a `runs_if` or `loop_back.condition` predicate
  fails to parse.

`--dry-run` returns the same failures without writing — iterate quickly.

### Artifact validator says my JSON is invalid

The validator registry is in `shared/artifact_validators.py`; each
schema has a Pydantic model in `shared/models/`. Match the schema name
(`review-verdict-schema` → `ReviewVerdict`,
`integration-test-report-schema` → `IntegrationTestReport`,
`plan-schema` → `Plan`). The model is the contract.

### Browser shows `dashboard: page not found`

The frontend is bundled into `dashboard/frontend/dist/` and served by
FastAPI. If `dist/` is empty, rebuild:

```bash
cd dashboard/frontend
pnpm install
pnpm build
```

---

## 9. Manual dogfood — run hammock on hammock

Stage 16's culminating goal: register the hammock repo as a hammock
project, submit a real `fix-bug` job, and watch the system fix one of
its own (intentionally introduced) bugs.

A pre-recorded toy target lives at `tests/fixtures/dogfood-bug/` —
read its `README.md` for the bug and the recorded ground-truth fix.
Steps:

1. Initialise the fixture as a real git repo (`git init`, commit, set
   remote, push to a throwaway GitHub repo). Run from the fixture dir.
2. `uv run hammock project register <abs-path-to-fixture>` (use
   `--skip-remote-checks` if you skipped the GitHub push).
3. `uv run hammock job submit --project dogfood-widget --type fix-bug
   --title "parse_range off-by-one" --request-file prompt.md`.
4. Open the dashboard, walk through the live view + HIL queue.
5. Once `summary.md` lands in the job dir, compare the resulting commit
   diff against `expected-fix.md` in the fixture.

What you discover during the walk that *isn't* in this runbook should
land as a v1+ backlog item in `docs/implementation.md § 9`.

---

## 10. Where to look next

- `docs/design.md` — canonical architecture (state machines, plane
  separation, MCP bridge, agent fleet).
- `docs/implementation.md` — stage-by-stage build plan + cross-cutting
  conventions (testing strategy, CI, parallel-stage execution).
- `docs/stages/stage-NN.md` — per-stage retrospective: what was built,
  what's locked for downstream, gotchas.
- `docs/stages/README.md` — index of all merged stages with their PR
  links.
