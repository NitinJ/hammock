# Hammock

> Agentic development harness — a single local system that orchestrates safe, observable, human-gated edits to source repositories, driven by a Claude session as the orchestrator and background agents as the workers.

**Status:** v0 (Stages 0–16 complete). The end-to-end lifecycle works
with either the **fake-fixture** runner (used by tests + the bundled
smoke) or the real `claude`-spawning `RealStageRunner` (selected
automatically when `HAMMOCK_FAKE_FIXTURES_DIR` is unset and `claude`
is on `$PATH`). Per-stage MCP server + Stop hook plumbing into the
driver entry point remains a v1+ item — see
`docs/implementation.md § 9`.

See [`docs/implementation.md`](docs/implementation.md) for the stage-by-stage plan and [`docs/design.md`](docs/design.md) for the canonical design. See [`docs/runbook.md`](docs/runbook.md) for the operator-facing reference.

## What it is

Hammock orchestrates safe, observable, human-gated edits to source repositories. It is the layer between the human (who sets goals, reviews, gates risky steps) and the agent fleet (which writes, tests, fixes, reviews code).

## Quickstart

### Prerequisites

- macOS or Linux, Python ≥ 3.12, `git` ≥ 2.40, [`gh`](https://cli.github.com/) ≥ 2.40, [`uv`](https://docs.astral.sh/uv/).

### Install

```bash
git clone https://github.com/NitinJ/hammock.git
cd hammock
uv sync --dev
```

### First run (fake-fixture lifecycle)

The fastest way to see the whole pipeline end-to-end is the bundled smoke,
which uses fake stage fixtures (no real Claude, no network):

```bash
uv run python scripts/manual-smoke-stage16.py
```

It registers a synthetic project, submits a `fix-bug` job, walks the
driver through every agent stage, resolves three human gates, and prints
the path to the resulting job dir for inspection.

### First run with the dashboard

For an interactive walk:

```bash
# 1. Start the dashboard (long-lived; keep this terminal open).
#    Set HAMMOCK_FAKE_FIXTURES_DIR to a fixtures dir if you want jobs
#    spawned through the dashboard to use FakeStageRunner.
uv run python -m dashboard
#    → http://127.0.0.1:8765/

# 2. In another terminal, register a project.
uv run hammock project register /path/to/your/repo

# 3. In the dashboard, open `/jobs/new`, fill the form, submit.
#    The dashboard's POST /api/jobs spawns the Job Driver as a side
#    effect and the Stage Live view will start streaming events.
#    (CLI `hammock job submit` only compiles + writes the job dir; it
#    does not spawn a driver. Use the dashboard for the watching flow.)
```

For everything else — health-check verbs, troubleshooting, the manual
dogfood walk-through, and the v1+ items required for a real `claude`
end-to-end run — see [`docs/runbook.md`](docs/runbook.md).

## Layout

```
hammock/
├── shared/        # Pydantic models + paths + atomic writes; cross-process contract
├── dashboard/     # the long-lived dashboard process (FastAPI + Vue 3)
├── job_driver/    # one OS process per active job
├── cli/           # `hammock project ...`, `hammock job ...`
├── tests/         # unit + integration + e2e
└── docs/          # design + implementation + runbook
```

## License

TBD.
