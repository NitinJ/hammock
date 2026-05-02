"""Task records.

Per design doc § Lifecycle § Task state machine. Tasks are engine-first-class
objects observed via MCP; persisted to
``jobs/<id>/stages/<sid>/tasks/<task_id>/task.json``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskState(StrEnum):
    """Task-level state machine."""

    RUNNING = "RUNNING"
    BLOCKED_ON_HUMAN = "BLOCKED_ON_HUMAN"
    STUCK = "STUCK"
    FAILED = "FAILED"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class TaskRecord(BaseModel):
    """Persisted task state."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    stage_id: str = Field(min_length=1)
    state: TaskState

    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None

    subagent_id: str | None = None
    branch: str | None = None
    worktree_path: str | None = None

    last_known_status: Literal["RUNNING", "DONE", "FAILED", "CANCELLED"] | None = None
    has_uncommitted_changes: bool = False
    head_commit: str | None = None

    cost_accrued: float = 0.0
    restart_count: int = 0
