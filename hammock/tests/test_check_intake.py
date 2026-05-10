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
