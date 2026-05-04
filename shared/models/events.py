"""Event taxonomy + envelope.

Per design doc § Observability and Observatory § Event stream. JSONL, append-only,
typed payloads, monotonic ``seq`` per source.

The envelope is generic; ``payload`` is an open dict. v0 keeps payload shapes
informal — typed payload models will land alongside the consumers that need
them (the dashboard cache, the cost roll-up, the Soul observer). The
``EVENT_TYPES`` constant pins the canonical taxonomy so producers and
consumers share a vocabulary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Event-type vocabulary (canonical; extending requires a stage)
# ---------------------------------------------------------------------------

EVENT_TYPES: frozenset[str] = frozenset(
    {
        # Lifecycle
        "job_state_transition",
        "stage_state_transition",
        "task_state_transition",
        # Agent0 session
        "agent0_session_spawned",
        "agent0_session_exited",
        "subagent_dispatched",
        "subagent_completed",
        "worker_heartbeat",
        # Tool use
        "tool_invoked",
        "tool_result_received",
        # HIL
        "hil_item_opened",
        "hil_item_routed",
        "hil_item_answered",
        # Channel chat
        "chat_message_sent_to_session",
        "chat_message_received_from_session",
        "engine_nudge_emitted",
        # Cost
        "cost_accrued",
        # Hooks + validators
        "hook_fired",
        "validator_passed",
        "validator_failed",
        # Soul / Council (v2+ but vocabulary fixed now)
        "proposal_emitted",
        "proposal_static_check",
        "reviewer_convened",
        "reviewer_verdict",
        "human_blessing",
        "proposal_applied",
        # Health
        "task_stuck_detected",
        "task_failure_recorded",
        "nudge_loop_exhausted",
        # Real-claude e2e precondition track (P4) — visibility for the
        # worktree lifecycle and per-stage subprocess exit codes.
        # Without these the e2e test has no contract-level signal for
        # "the subprocess actually exited cleanly" or "the worktree was
        # registered/cleaned up".
        "worktree_created",
        "worktree_destroyed",
        "worker_exit",
    }
)


EventSource = Literal[
    "job_driver",
    "agent0",
    "subagent",
    "dashboard",
    "human",
    "engine",
    "hook",
]


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


class Event(BaseModel):
    """Append-only event envelope.

    ``seq`` is monotonic per source-and-scope (not globally). Consumers use
    ``Last-Event-ID: <seq>`` semantics on a per-scope SSE channel for replay.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=0)
    timestamp: datetime
    event_type: str = Field(min_length=1)

    source: EventSource

    job_id: str = Field(min_length=1)
    stage_id: str | None = None
    task_id: str | None = None
    subagent_id: str | None = None
    parent_event_seq: int | None = None

    payload: dict[str, Any] = Field(default_factory=dict)
