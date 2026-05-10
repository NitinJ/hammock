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


def _evaluate(job_dir: Path) -> str | None:
    """Return a `reason` string when the orchestrator is stale, else None."""
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

    job_dir = _resolve_job_dir()
    reason = _evaluate(job_dir)
    if reason is None:
        sys.exit(0)

    msg_tail = (
        " Read $JOB_DIR/orchestrator_messages.jsonl and $JOB_DIR/control.md, "
        "process them per Steps A and B of the orchestrator main loop, then continue."
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
