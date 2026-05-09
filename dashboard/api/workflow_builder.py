"""Workflow-builder session API.

The dev opens the workflow editor and clicks "Talk to builder agent."
The frontend POSTs a session, then sends turns. Each turn synchronously
spawns a `claude -p` invocation with the builder prompt + history +
current draft yaml + the new user text. The agent's response (with any
proposed yaml extracted) is appended to the session's messages.jsonl.

Sessions live under ``<root>/builder-sessions/<session_id>/``:

    meta.json
    messages.jsonl
    current.yaml
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import secrets
from pathlib import Path
from typing import Any

import yaml as _yaml
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ValidationError

from dashboard.runner.builder import (
    ClaudeRunner,
    extract_proposed_yaml,
    spawn_builder_turn,
)
from dashboard.settings import load_settings
from hammock.engine.workflow import Workflow

log = logging.getLogger(__name__)

router = APIRouter()

_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9]{8,32}$")
_DEFAULT_STARTER_YAML = """name: my-workflow
description: |
  Describe what this workflow does.

nodes:
  - id: write-bug-report
    prompt: write-bug-report
    requires:
      - output.md
"""


# ---------------------------------------------------------------------------
# Test seam — overridable claude runner
# ---------------------------------------------------------------------------

_runner_override: ClaudeRunner | None = None


def set_claude_runner(runner: ClaudeRunner | None) -> None:
    """Test seam: route spawn_builder_turn through ``runner`` instead
    of subprocess.run."""
    global _runner_override
    _runner_override = runner


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


def _sessions_root(root: Path) -> Path:
    return root / "builder-sessions"


def _session_dir(session_id: str, root: Path) -> Path:
    if not _SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="invalid session_id")
    return _sessions_root(root) / session_id


def _meta_path(session_id: str, root: Path) -> Path:
    return _session_dir(session_id, root) / "meta.json"


def _messages_path(session_id: str, root: Path) -> Path:
    return _session_dir(session_id, root) / "messages.jsonl"


def _yaml_path(session_id: str, root: Path) -> Path:
    return _session_dir(session_id, root) / "current.yaml"


def _read_messages(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                out.append(obj)
        except json.JSONDecodeError:
            continue
    return out


def _append_message(path: Path, message: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(message) + "\n")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat()


# ---------------------------------------------------------------------------
# Schema validation for proposed yaml
# ---------------------------------------------------------------------------


def _validate_workflow_yaml(yaml_text: str) -> tuple[bool, str | None]:
    """Returns (ok, error_message_or_none)."""
    if not yaml_text or not yaml_text.strip():
        return False, "empty yaml"
    try:
        raw = _yaml.safe_load(yaml_text)
    except _yaml.YAMLError as exc:
        return False, f"yaml parse error: {exc}"
    if not isinstance(raw, dict):
        return False, "yaml top-level must be a mapping"
    try:
        Workflow.model_validate(raw)
    except ValidationError as exc:
        return False, f"schema invalid: {exc}"
    return True, None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateSessionBody(BaseModel):
    project_slug: str | None = None
    workflow_name: str | None = None
    starting_yaml: str | None = None


class SendMessageBody(BaseModel):
    text: str


class ApplyBody(BaseModel):
    proposed_yaml: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/workflow-builder/sessions")
def create_session(
    body: CreateSessionBody = Body(default_factory=CreateSessionBody),  # noqa: B008
) -> dict[str, Any]:
    settings = load_settings()
    session_id = secrets.token_hex(8)  # 16 chars hex
    sd = _session_dir(session_id, settings.root)
    sd.mkdir(parents=True, exist_ok=True)

    meta = {
        "session_id": session_id,
        "created_at": _now_iso(),
        "project_slug": body.project_slug,
        "workflow_name": body.workflow_name,
    }
    _meta_path(session_id, settings.root).write_text(json.dumps(meta, indent=2))

    starting = body.starting_yaml.strip() if body.starting_yaml else _DEFAULT_STARTER_YAML
    _yaml_path(session_id, settings.root).write_text(starting)
    # ensure messages file exists
    _messages_path(session_id, settings.root).touch()

    return {"session_id": session_id, "current_yaml": starting, "messages": []}


@router.get("/workflow-builder/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    settings = load_settings()
    sd = _session_dir(session_id, settings.root)
    if not sd.is_dir():
        raise HTTPException(status_code=404, detail="session not found")
    meta_p = _meta_path(session_id, settings.root)
    meta = json.loads(meta_p.read_text()) if meta_p.is_file() else {}
    messages = _read_messages(_messages_path(session_id, settings.root))
    yaml_p = _yaml_path(session_id, settings.root)
    current_yaml = yaml_p.read_text() if yaml_p.is_file() else ""
    return {
        "session_id": session_id,
        "meta": meta,
        "messages": messages,
        "current_yaml": current_yaml,
    }


@router.post("/workflow-builder/sessions/{session_id}/messages")
def send_message(session_id: str, body: SendMessageBody) -> dict[str, Any]:
    settings = load_settings()
    sd = _session_dir(session_id, settings.root)
    if not sd.is_dir():
        raise HTTPException(status_code=404, detail="session not found")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    messages_p = _messages_path(session_id, settings.root)

    # Append the user's message first.
    user_msg = {
        "id": secrets.token_hex(6),
        "from": "user",
        "timestamp": _now_iso(),
        "text": text,
    }
    _append_message(messages_p, user_msg)

    # Spawn the builder turn. The helper reads the current.yaml + history.
    result = spawn_builder_turn(
        session_dir=sd,
        user_text=text,
        claude_binary=settings.claude_binary,
        runner=_runner_override,
    )

    agent_text = str(result.get("text", "") or "")
    proposed = result.get("proposed_yaml")
    proposed_yaml = proposed if isinstance(proposed, str) and proposed.strip() else None

    # Validate the proposal — reject (clear field) if it doesn't pass schema.
    schema_note: str | None = None
    if proposed_yaml is not None:
        ok, err = _validate_workflow_yaml(proposed_yaml)
        if not ok:
            schema_note = (
                "\n\n_(Note: my proposed yaml didn't validate against the schema "
                f"and was not offered as Apply. Error: {err})_"
            )
            proposed_yaml = None

    if schema_note:
        agent_text = (agent_text or "") + schema_note

    agent_msg: dict[str, Any] = {
        "id": secrets.token_hex(6),
        "from": "agent",
        "timestamp": _now_iso(),
        "text": agent_text,
    }
    if proposed_yaml is not None:
        agent_msg["proposed_yaml"] = proposed_yaml
    _append_message(messages_p, agent_msg)

    return {
        "user_message": user_msg,
        "agent_message": agent_msg,
    }


@router.post("/workflow-builder/sessions/{session_id}/apply")
def apply_proposal(session_id: str, body: ApplyBody) -> dict[str, Any]:
    settings = load_settings()
    sd = _session_dir(session_id, settings.root)
    if not sd.is_dir():
        raise HTTPException(status_code=404, detail="session not found")
    ok, err = _validate_workflow_yaml(body.proposed_yaml)
    if not ok:
        raise HTTPException(status_code=400, detail=err or "invalid yaml")
    _yaml_path(session_id, settings.root).write_text(body.proposed_yaml)
    return {"ok": "true", "current_yaml": body.proposed_yaml}


@router.delete("/workflow-builder/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, Any]:
    import contextlib

    settings = load_settings()
    sd = _session_dir(session_id, settings.root)
    if sd.is_dir():
        # Best-effort cleanup; ignore individual file errors.
        for child in sd.iterdir():
            with contextlib.suppress(OSError):
                child.unlink()
        with contextlib.suppress(OSError):
            sd.rmdir()
    return {"ok": "true"}


__all__ = [
    "extract_proposed_yaml",
    "router",
    "set_claude_runner",
]
