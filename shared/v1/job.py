"""Job model — Hammock v1.

Per design-patch §1. Lightweight: a job is just a workflow YAML + a
job-request input + a state-machine. Loops, substrate, HIL all live
elsewhere; this module just owns the envelope around a workflow run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class JobState(StrEnum):
    SUBMITTED = "submitted"
    """Initial state immediately after `hammock job submit`."""

    RUNNING = "running"
    """Driver is actively executing nodes."""

    BLOCKED_ON_HUMAN = "blocked_on_human"
    """One or more HIL nodes are awaiting human input. Driver continues
    to wait in-process (per design-patch §3); this state is a status
    signal for the dashboard, not a process-exit trigger."""

    COMPLETED = "completed"
    """All declared variables produced or correctly skipped; terminal."""

    FAILED = "failed"
    """A node failed its contract or exhausted retries; terminal."""

    CANCELLED = "cancelled"
    """Operator or supervisor abandoned the job; terminal."""


class JobConfig(BaseModel):
    """Persisted as <job_dir>/job.json."""

    model_config = ConfigDict(extra="forbid")

    job_slug: str
    """Stable id (typically date-prefixed: ``2026-05-05-fix-bug-foo``)."""

    workflow_name: str
    """The `workflow:` value from the loaded YAML."""

    workflow_path: str
    """Absolute path to the workflow YAML file used at submission."""

    state: JobState
    repo_slug: str | None = None
    """Repo identity if any node uses code substrate. None for artifact-only
    workflows (T1)."""

    submitted_at: datetime
    updated_at: datetime


def make_job_config(
    *,
    job_slug: str,
    workflow_name: str,
    workflow_path: Path,
    repo_slug: str | None,
    now: datetime | None = None,
) -> JobConfig:
    ts = now or datetime.now(UTC)
    return JobConfig(
        job_slug=job_slug,
        workflow_name=workflow_name,
        workflow_path=str(workflow_path),
        state=JobState.SUBMITTED,
        repo_slug=repo_slug,
        submitted_at=ts,
        updated_at=ts,
    )


class NodeRunState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class NodeRun(BaseModel):
    """Persisted per-node state — <job_dir>/nodes/<node_id>/state.json.

    Tracks attempt count, current state, last-error if failed."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    state: NodeRunState
    attempts: int = Field(default=0, ge=0)
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


def make_node_run(node_id: str, *, now: datetime | None = None) -> NodeRun:
    return NodeRun(
        node_id=node_id,
        state=NodeRunState.PENDING,
        attempts=0,
        started_at=None,
        finished_at=None,
    )
