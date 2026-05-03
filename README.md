# Hammock

> Agentic development harness — a single local system that orchestrates safe, observable, human-gated edits to source repositories, driven by a Claude session as the orchestrator and background agents as the workers.

**Status:** v0 (Stages 0–16 complete). See [`docs/implementation.md`](docs/implementation.md) for the stage-by-stage plan and [`docs/design.md`](docs/design.md) for the canonical design. See [`docs/runbook.md`](docs/runbook.md) for the operator-facing reference.

## What it is

Hammock orchestrates safe, observable, human-gated edits to source repositories. It is the layer between the human (who sets goals, reviews, gates risky steps) and the agent fleet (which writes, tests, fixes, reviews code).

## Quickstart

### Prerequisites

- macOS or Linux, Python ≥ 3.12, `git` ≥ 2.40, [`gh`](https://cli.github.com/) ≥ 2.40, [`uv`](https://docs.astral.sh/uv/).
- A `claude` CLI in `$PATH` for real (non-fake) job runs.

### Install

```bash
git clone https://github.com/NitinJ/hammock.git
cd hammock
uv sync --dev
```

### First run

```bash
# 1. Start the dashboard (long-lived, keep this terminal open).
uv run python -m dashboard
#    → http://127.0.0.1:8765/

# 2. In another terminal, register a project.
uv run hammock project register /path/to/your/repo

# 3. Submit a job.
uv run hammock job submit \
    --project <slug> \
    --type fix-bug \
    --title "Short title" \
    --request-text "Paragraph describing the goal."

# 4. Open the dashboard, click into the job, watch the live stage view,
#    answer HIL prompts as they appear.
```

For everything else — health-check verbs, troubleshooting, the manual
dogfood walk-through, and how to operate hammock in production —
see [`docs/runbook.md`](docs/runbook.md).

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
