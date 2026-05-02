# Hammock

> Agentic development harness — a single local system that orchestrates safe, observable, human-gated edits to source repositories, driven by a Claude session as the orchestrator and background agents as the workers.

**Status:** v0 in progress. See [`docs/implementation.md`](docs/implementation.md) for the stage-by-stage plan and [`docs/design.md`](docs/design.md) for the canonical design.

## What it is

Hammock orchestrates safe, observable, human-gated edits to source repositories. It is the layer between the human (who sets goals, reviews, gates risky steps) and the agent fleet (which writes, tests, fixes, reviews code).

## Quickstart

> Quickstart will land with Stage 16 (E2E + dogfood). Until then, this README is a placeholder for the documentation that will live here.

## Layout

```
hammock/
├── shared/        # Pydantic models + paths + atomic writes; cross-process contract
├── dashboard/     # the long-lived dashboard process (FastAPI + Vue 3)
├── job_driver/    # one OS process per active job
├── cli/           # `hammock project ...`, `hammock job ...`
├── tests/         # unit + integration + e2e
└── docs/          # design + implementation + (eventually) runbook
```

## License

TBD.
