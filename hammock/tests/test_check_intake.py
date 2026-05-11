"""Unit tests for the orchestrator intake-discipline hook.

The hook is invoked by Claude Code with a JSON payload on stdin and is
expected to either exit 0 silently (allow) or print a structured
``{"decision":"block","reason":"…"}`` JSON object on stdout (block).

We invoke ``hammock/hooks/check_intake.py`` as a subprocess to exercise
the real CLI contract — same path the runner wires into
``.claude/settings.json``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "check_intake.py"


@pytest.fixture
def job_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Make a job-dir-shaped tmpdir and point HAMMOCK_JOB_DIR at it."""
    monkeypatch.setenv("HAMMOCK_JOB_DIR", str(tmp_path))
    yield tmp_path


def _run_hook(payload: dict[str, object], job_dir: Path) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["HAMMOCK_JOB_DIR"] = str(job_dir)
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _write_state(job_dir: Path, *, last_msg_id: str | None, last_control: str) -> None:
    (job_dir / "orchestrator_state.json").write_text(
        json.dumps(
            {
                "last_processed_msg_id": last_msg_id,
                "last_control_state": last_control,
                "active_tasks": [],
                "active_helpers": [],
                "completed_nodes": [],
                "failed_nodes": [],
                "skipped_nodes": [],
                "human_review_iterations": {},
                "expanded_nodes": {},
            }
        )
    )


def _write_messages(job_dir: Path, ids: list[str]) -> None:
    lines = [
        json.dumps({"id": mid, "from": "operator", "timestamp": "x", "text": "x"}) for mid in ids
    ]
    (job_dir / "orchestrator_messages.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""))


def _write_control(job_dir: Path, state: str) -> None:
    (job_dir / "control.md").write_text(
        f"---\nstate: {state}\nrequested_at: x\nrequested_by: x\n---\n"
    )


def test_no_state_file_allows_action(job_dir: Path) -> None:
    """First-iteration / pre-spawn case: state.json doesn't exist yet,
    so there is nothing to enforce. Hook must exit 0 silently."""
    rc, out, _ = _run_hook({"hook_event_name": "Stop"}, job_dir)
    assert rc == 0
    assert out.strip() == ""


def test_fresh_state_allows_stop(job_dir: Path) -> None:
    """state.json says it's processed everything; hook allows the Stop."""
    _write_state(job_dir, last_msg_id="msg-3", last_control="running")
    _write_messages(job_dir, ["msg-1", "msg-2", "msg-3"])
    _write_control(job_dir, "running")

    rc, out, _ = _run_hook({"hook_event_name": "Stop"}, job_dir)
    assert rc == 0
    assert out.strip() == ""


def test_unread_message_blocks_stop(job_dir: Path) -> None:
    """An operator message arrived after the orchestrator's last intake.
    Hook must block Stop with a structured reason."""
    _write_state(job_dir, last_msg_id="msg-2", last_control="running")
    _write_messages(job_dir, ["msg-1", "msg-2", "msg-3"])  # msg-3 is unread
    _write_control(job_dir, "running")

    rc, out, _ = _run_hook({"hook_event_name": "Stop"}, job_dir)
    assert rc == 0
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "msg-3" in decision["reason"]
    # The reason guides the model toward the correct fix.
    assert "orchestrator_messages.jsonl" in decision["reason"]


def test_paused_control_blocks_stop(job_dir: Path) -> None:
    """control.md flipped to paused but the orchestrator hasn't observed
    the transition yet. Hook must block Stop."""
    _write_state(job_dir, last_msg_id=None, last_control="running")
    _write_control(job_dir, "paused")

    rc, out, _ = _run_hook({"hook_event_name": "Stop"}, job_dir)
    assert rc == 0
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "paused" in decision["reason"]
    assert "control.md" in decision["reason"]


def test_unread_message_blocks_task_dispatch(job_dir: Path) -> None:
    """PreToolUse on Task should block dispatch when there's an unread
    operator message — the orchestrator must not progress nodes while
    ignoring the operator."""
    _write_state(job_dir, last_msg_id="msg-1", last_control="running")
    _write_messages(job_dir, ["msg-1", "msg-2"])  # msg-2 unread
    _write_control(job_dir, "running")

    rc, out, _ = _run_hook(
        {"hook_event_name": "PreToolUse", "tool_name": "Task"},
        job_dir,
    )
    assert rc == 0
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "msg-2" in decision["reason"]


def test_pretooluse_other_tools_pass_through(job_dir: Path) -> None:
    """Read/Write/Bash etc. are not Task — the hook must NOT block these
    even when intake is stale, because doing so would prevent the
    orchestrator from reading the very files we want it to read."""
    _write_state(job_dir, last_msg_id=None, last_control="running")
    _write_control(job_dir, "paused")  # stale
    _write_messages(job_dir, ["msg-1"])

    for tool in ("Read", "Write", "Edit", "Bash", "Glob", "Grep"):
        rc, out, _ = _run_hook(
            {"hook_event_name": "PreToolUse", "tool_name": tool},
            job_dir,
        )
        assert rc == 0, tool
        assert out.strip() == "", tool


def test_unknown_event_passes_through(job_dir: Path) -> None:
    """Unknown / unhandled hook events must not block."""
    _write_state(job_dir, last_msg_id=None, last_control="paused")
    _write_control(job_dir, "running")  # also stale (running != paused)

    rc, out, _ = _run_hook({"hook_event_name": "PostToolUse"}, job_dir)
    assert rc == 0
    assert out.strip() == ""


def test_schedule_wakeup_always_denied(job_dir: Path) -> None:
    """The orchestrator must never call ScheduleWakeup. `claude -p` has no
    harness to re-invoke the agent when the wakeup fires, so ScheduleWakeup
    cleanly exits the process and the runner falsely marks the job complete.

    Hook denies it unconditionally, even when state is otherwise fresh."""
    _write_state(job_dir, last_msg_id=None, last_control="running")
    _write_control(job_dir, "running")

    rc, out, _ = _run_hook(
        {"hook_event_name": "PreToolUse", "tool_name": "ScheduleWakeup"},
        job_dir,
    )
    assert rc == 0
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "ScheduleWakeup" in decision["reason"]
    assert "Bash sleep 1" in decision["reason"]


def test_stop_blocked_when_active_tasks_pending(job_dir: Path) -> None:
    """A clean rc=0 exit is wrong when work is in flight. Stop hook must
    block when active_tasks is non-empty."""
    state_payload = {
        "last_processed_msg_id": None,
        "last_control_state": "running",
        "active_tasks": [
            {"node_id": "write-bug-report", "task_id": "t-1", "started_at": "x", "attempt": 1}
        ],
        "active_helpers": [],
        "completed_nodes": [],
        "failed_nodes": [],
        "skipped_nodes": [],
        "human_review_iterations": {},
        "expanded_nodes": {},
    }
    (job_dir / "orchestrator_state.json").write_text(json.dumps(state_payload))
    _write_control(job_dir, "running")

    rc, out, _ = _run_hook({"hook_event_name": "Stop"}, job_dir)
    assert rc == 0
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "active_tasks" in decision["reason"]
    assert "write-bug-report" in decision["reason"]


def test_stop_blocked_when_active_helpers_pending(job_dir: Path) -> None:
    """Helper Tasks count too — they're spawned by the orchestrator and
    we cannot end the turn while one is still running."""
    state_payload = {
        "last_processed_msg_id": None,
        "last_control_state": "running",
        "active_tasks": [],
        "active_helpers": [
            {
                "helper": "prepare-node-input",
                "for_node": "write-bug-report",
                "task_id": "h-1",
                "started_at": "x",
                "context": {"trigger": "dispatch"},
            }
        ],
        "completed_nodes": [],
        "failed_nodes": [],
        "skipped_nodes": [],
        "human_review_iterations": {},
        "expanded_nodes": {},
    }
    (job_dir / "orchestrator_state.json").write_text(json.dumps(state_payload))
    _write_control(job_dir, "running")

    rc, out, _ = _run_hook({"hook_event_name": "Stop"}, job_dir)
    assert rc == 0
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "active_helpers" in decision["reason"]
    assert "prepare-node-input" in decision["reason"]


def test_stop_blocked_when_pending_nodes_remain(job_dir: Path) -> None:
    """If active_tasks is empty but the workflow has pending nodes that
    haven't been dispatched, the orchestrator must keep working — not stop."""
    state_payload = {
        "last_processed_msg_id": None,
        "last_control_state": "running",
        "active_tasks": [],
        "active_helpers": [],
        "completed_nodes": ["write-bug-report"],
        "failed_nodes": [],
        "skipped_nodes": [],
        "human_review_iterations": {},
        "expanded_nodes": {},
    }
    (job_dir / "orchestrator_state.json").write_text(json.dumps(state_payload))
    _write_control(job_dir, "running")
    # Static workflow has more nodes than just write-bug-report.
    (job_dir / "workflow.yaml").write_text(
        "name: x\n"
        "nodes:\n"
        "  - id: write-bug-report\n"
        "    prompt: write-bug-report\n"
        "  - id: write-design-spec\n"
        "    prompt: write-design-spec\n"
        "    after: [write-bug-report]\n"
    )

    rc, out, _ = _run_hook({"hook_event_name": "Stop"}, job_dir)
    assert rc == 0
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "pending nodes" in decision["reason"]
    assert "write-design-spec" in decision["reason"]


def test_stop_allowed_when_all_terminal(job_dir: Path) -> None:
    """The legitimate completion case: every workflow node terminal,
    no in-flight tasks/helpers. Stop is allowed."""
    state_payload = {
        "last_processed_msg_id": None,
        "last_control_state": "running",
        "active_tasks": [],
        "active_helpers": [],
        "completed_nodes": ["write-bug-report", "write-summary"],
        "failed_nodes": [],
        "skipped_nodes": [],
        "human_review_iterations": {},
        "expanded_nodes": {},
    }
    (job_dir / "orchestrator_state.json").write_text(json.dumps(state_payload))
    _write_control(job_dir, "running")
    (job_dir / "workflow.yaml").write_text(
        "name: x\n"
        "nodes:\n"
        "  - id: write-bug-report\n"
        "    prompt: write-bug-report\n"
        "  - id: write-summary\n"
        "    prompt: write-summary\n"
        "    after: [write-bug-report]\n"
    )

    rc, out, _ = _run_hook({"hook_event_name": "Stop"}, job_dir)
    assert rc == 0
    assert out.strip() == ""


def test_pretooluse_task_does_not_block_on_pending_nodes(job_dir: Path) -> None:
    """PreToolUse on Task is about intake staleness, NOT about pending
    work — blocking dispatch when there's pending work would prevent
    the orchestrator from making progress at all."""
    state_payload = {
        "last_processed_msg_id": None,
        "last_control_state": "running",
        "active_tasks": [],
        "active_helpers": [],
        "completed_nodes": [],
        "failed_nodes": [],
        "skipped_nodes": [],
        "human_review_iterations": {},
        "expanded_nodes": {},
    }
    (job_dir / "orchestrator_state.json").write_text(json.dumps(state_payload))
    _write_control(job_dir, "running")
    (job_dir / "workflow.yaml").write_text(
        "name: x\nnodes:\n  - id: write-bug-report\n    prompt: write-bug-report\n"
    )

    rc, out, _ = _run_hook(
        {"hook_event_name": "PreToolUse", "tool_name": "Task"},
        job_dir,
    )
    assert rc == 0
    assert out.strip() == ""


def test_only_operator_messages_count(job_dir: Path) -> None:
    """The hook tracks operator messages; orchestrator's own self-replies
    must not trip the guard."""
    _write_state(job_dir, last_msg_id="msg-1", last_control="running")
    (job_dir / "orchestrator_messages.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"id": "msg-1", "from": "operator", "text": "x"}),
                json.dumps({"id": "msg-2", "from": "orchestrator", "text": "got it"}),
            ]
        )
        + "\n"
    )
    _write_control(job_dir, "running")

    rc, out, _ = _run_hook({"hook_event_name": "Stop"}, job_dir)
    assert rc == 0
    assert out.strip() == ""
