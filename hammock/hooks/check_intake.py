#!/usr/bin/env python3
"""Orchestrator intake-discipline hook.

Wired into the orchestrator subprocess (``claude -p``) at two events:

- ``Stop`` — fires when the orchestrator wants to end its turn.
- ``PreToolUse`` (matcher ``Task``) — fires before each ``Task`` dispatch.

The hook compares the on-disk ``orchestrator_messages.jsonl`` and
``control.md`` against the orchestrator's own ``orchestrator_state.json``.
If the operator has appended a new message OR flipped control.md to a
different lifecycle state and the orchestrator has not yet acknowledged
that change, we emit ``{"decision":"block","reason":"…"}`` so Claude
keeps the turn alive (Stop) or aborts the dispatch (PreToolUse) and
re-reads the two files.

The orchestrator prompt instructs the model to do these reads at the
top of every iteration; this hook is the hard contract that backs the
soft prompt instruction.

Locating the job dir
--------------------

The runner sets ``HAMMOCK_JOB_DIR`` as an env var on the orchestrator
process, and ``claude`` propagates it to hook subprocesses. We fall
back to the cwd if the env var is missing, since the orchestrator runs
with ``cwd=<job_dir>``.

Hook IO contract
----------------

Hooks receive JSON on stdin with at least ``hook_event_name`` and (for
PreToolUse) ``tool_name``. They emit JSON on stdout to communicate a
structured response, or write nothing and exit 0 to allow the action.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _resolve_job_dir() -> Path:
    env = os.environ.get("HAMMOCK_JOB_DIR")
    if env:
        return Path(env)
    return Path.cwd()


def _highest_operator_msg_id(msgs_path: Path) -> str | None:
    if not msgs_path.is_file():
        return None
    highest: str | None = None
    for raw in msgs_path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if entry.get("from") != "operator":
            continue
        mid = entry.get("id")
        if not isinstance(mid, str):
            continue
        if highest is None or mid > highest:
            highest = mid
    return highest


def _control_state(control_path: Path) -> str | None:
    if not control_path.is_file():
        return None
    text = control_path.read_text()
    in_frontmatter = False
    for raw in text.splitlines():
        line = raw.strip()
        if line == "---":
            if in_frontmatter:
                break
            in_frontmatter = True
            continue
        if not in_frontmatter:
            continue
        if line.startswith("state:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return None


def _workflow_node_ids(job_dir: Path) -> list[str]:
    """Return the list of node ids declared by the static workflow.yaml.

    We parse the yaml by hand to avoid pulling pyyaml into the hook
    runtime (the hook must be stdlib-only so it runs without the venv).
    """
    wf_path = job_dir / "workflow.yaml"
    if not wf_path.is_file():
        return []
    ids: list[str] = []
    in_nodes = False
    for raw in wf_path.read_text().splitlines():
        line = raw.rstrip()
        if line.startswith("nodes:"):
            in_nodes = True
            continue
        if not in_nodes:
            continue
        stripped = line.strip()
        if not stripped.startswith("- id:") and not stripped.startswith("id:"):
            continue
        if stripped.startswith("- id:"):
            value = stripped[len("- id:") :].strip()
        else:
            value = stripped[len("id:") :].strip()
        value = value.strip("\"'")
        if value:
            ids.append(value)
    return ids


def _evaluate(job_dir: Path, *, on_stop: bool) -> str | None:
    """Return a `reason` string when the orchestrator should be blocked, else None.

    Two failure modes both render the orchestrator broken from the operator's
    perspective:

    1. **Intake staleness** — orchestrator's state.json hasn't observed a new
       operator message or a flipped control.md. Applies to Stop and
       PreToolUse:Task.
    2. **Premature stop with work pending** — Stop hook only. active_tasks or
       active_helpers are non-empty, or some workflow node is still pending
       and not yet in completed/failed/skipped. Ending the turn here lets
       `claude -p` exit cleanly and the runner mark the job 'completed'
       while subagents are still in flight.
    """
    state_path = job_dir / "orchestrator_state.json"
    if not state_path.is_file():
        # Job hasn't initialized state yet; nothing to enforce.
        return None
    try:
        state = json.loads(state_path.read_text())
    except Exception:
        return None

    last_msg_id = state.get("last_processed_msg_id")
    last_control = state.get("last_control_state")

    highest_msg_id = _highest_operator_msg_id(job_dir / "orchestrator_messages.jsonl")
    current_control = _control_state(job_dir / "control.md")

    reasons: list[str] = []

    if highest_msg_id is not None and highest_msg_id != last_msg_id:
        reasons.append(
            f"unprocessed operator message {highest_msg_id} (last_processed_msg_id={last_msg_id!r})"
        )
    if current_control is not None and current_control != last_control:
        reasons.append(
            f"control.md state is {current_control!r} but state.json says "
            f"last_control_state={last_control!r}"
        )

    if on_stop:
        active_tasks = state.get("active_tasks") or []
        active_helpers = state.get("active_helpers") or []
        completed = set(state.get("completed_nodes") or [])
        failed = set(state.get("failed_nodes") or [])
        skipped = set(state.get("skipped_nodes") or [])
        expanded = set(state.get("expanded_nodes") or {})

        if active_tasks:
            task_ids = ", ".join(t.get("node_id", "?") for t in active_tasks)
            reasons.append(f"active_tasks non-empty (in flight: {task_ids})")
        if active_helpers:
            helper_names = ", ".join(
                f"{h.get('helper', '?')}({h.get('for_node', '?')})" for h in active_helpers
            )
            reasons.append(f"active_helpers non-empty (in flight: {helper_names})")

        terminal = completed | failed | skipped
        # Pending = workflow nodes not yet terminal AND not expanded out yet.
        # Expanded children are part of the runtime DAG; if their parent
        # expander has not yet produced expansion.yaml, the parent itself
        # is in active_tasks/state.md=preparing, which the active_tasks
        # check covers.
        all_nodes = set(_workflow_node_ids(job_dir)) | expanded
        pending = [nid for nid in all_nodes if nid not in terminal]
        if pending and not active_tasks and not active_helpers:
            pending_str = ", ".join(sorted(pending)[:5])
            more = "" if len(pending) <= 5 else f" (+{len(pending) - 5} more)"
            reasons.append(f"pending nodes remain with nothing dispatched: {pending_str}{more}")

    if not reasons:
        return None
    return "; ".join(reasons)


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        payload = {}

    hook_event = payload.get("hook_event_name", "")
    tool_name = payload.get("tool_name", "")

    # Always deny ScheduleWakeup. The orchestrator must loop inline via
    # `Bash sleep 1` within its own `claude -p` turn; ScheduleWakeup is a
    # ``claude`` harness primitive that ends the turn and asks the harness
    # to wake up later. `claude -p` has no harness, so the process exits
    # cleanly and the runner marks the job 'completed' even though active
    # subagents are still running.
    if hook_event == "PreToolUse" and tool_name == "ScheduleWakeup":
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": (
                        "ScheduleWakeup is not available to the orchestrator. "
                        "Use `Bash sleep 1` and loop back to Step A within this same "
                        "turn. `claude -p` has no harness to re-invoke you — calling "
                        "ScheduleWakeup ends the orchestrator process while subagents "
                        "are still running and the runner falsely marks the job complete."
                    ),
                }
            )
        )
        sys.exit(0)

    job_dir = _resolve_job_dir()
    reason = _evaluate(job_dir, on_stop=(hook_event == "Stop"))
    if reason is None:
        sys.exit(0)

    msg_tail = (
        " Read $JOB_DIR/orchestrator_messages.jsonl and $JOB_DIR/control.md, "
        "process them per Steps A and B of the orchestrator main loop, "
        "then continue with `Bash sleep 1` + Step C/D/E. Do not end the "
        "turn until every workflow node is in completed/failed/skipped."
    )

    if hook_event == "Stop":
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": f"Cannot end turn: {reason}." + msg_tail,
                }
            )
        )
        sys.exit(0)

    if hook_event == "PreToolUse" and tool_name == "Task":
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": f"Cannot dispatch Task: {reason}." + msg_tail,
                }
            )
        )
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
