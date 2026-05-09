"""Tests for the workflow-builder session API.

Uses a fake claude runner so no real LLM tokens are spent and the
results are deterministic.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard_v2.api.app import create_app
from dashboard_v2.api.workflow_builder import set_claude_runner
from dashboard_v2.runner.builder import (
    ClaudeRunner,
    assemble_builder_prompt,
    extract_proposed_yaml,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("HAMMOCK_V2_ROOT", str(tmp_path))
    app = create_app()
    with TestClient(app) as c:
        yield c
    set_claude_runner(None)


def _fake_runner(canned_text: str) -> ClaudeRunner:
    def runner(args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
        # Mirror claude --output-format json shape: {"type":"result","result":"<text>"}
        payload = json.dumps({"type": "result", "result": canned_text}).encode("utf-8")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=payload, stderr=b"")

    return runner


_VALID_PROPOSAL = """name: my-workflow
description: |
  A tiny test workflow.
nodes:
  - id: write
    prompt: write-bug-report
    requires:
      - output.md
"""

_INVALID_PROPOSAL = """name: my-workflow
nodes: []
"""  # nodes must be non-empty per Workflow schema


def test_create_session_returns_id_and_default_yaml(client: TestClient) -> None:
    r = client.post("/api/workflow-builder/sessions", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data["session_id"], str)
    assert len(data["session_id"]) >= 8
    assert "name:" in data["current_yaml"]
    assert data["messages"] == []


def test_create_session_uses_provided_starting_yaml(client: TestClient) -> None:
    starting = "name: foo\ndescription: x\nnodes:\n  - id: a\n    prompt: write-bug-report\n"
    r = client.post(
        "/api/workflow-builder/sessions",
        json={"starting_yaml": starting, "project_slug": "proj"},
    )
    assert r.status_code == 200
    sid = r.json()["session_id"]
    g = client.get(f"/api/workflow-builder/sessions/{sid}")
    assert g.status_code == 200
    assert "id: a" in g.json()["current_yaml"]
    assert g.json()["meta"]["project_slug"] == "proj"


def test_send_message_appends_both_messages_and_extracts_proposal(client: TestClient) -> None:
    canned = f"Sure — here's a starter workflow:\n\n```yaml workflow\n{_VALID_PROPOSAL}```\n"
    set_claude_runner(_fake_runner(canned))

    r = client.post("/api/workflow-builder/sessions", json={})
    sid = r.json()["session_id"]

    sm = client.post(
        f"/api/workflow-builder/sessions/{sid}/messages",
        json={"text": "build me a simple workflow"},
    )
    assert sm.status_code == 200, sm.text
    body = sm.json()
    assert body["user_message"]["text"] == "build me a simple workflow"
    assert body["user_message"]["from"] == "user"
    assert body["agent_message"]["from"] == "agent"
    assert "Sure" in body["agent_message"]["text"]
    # The fenced proposal extracts and survives schema validation.
    assert "name: my-workflow" in body["agent_message"]["proposed_yaml"]

    # Session state reflects both messages.
    g = client.get(f"/api/workflow-builder/sessions/{sid}").json()
    assert len(g["messages"]) == 2
    assert g["messages"][0]["from"] == "user"
    assert g["messages"][1]["from"] == "agent"


def test_send_message_strips_proposal_when_schema_invalid(client: TestClient) -> None:
    canned = "Here's an invalid one:\n\n```yaml workflow\n" + _INVALID_PROPOSAL + "```\n"
    set_claude_runner(_fake_runner(canned))

    r = client.post("/api/workflow-builder/sessions", json={})
    sid = r.json()["session_id"]

    sm = client.post(
        f"/api/workflow-builder/sessions/{sid}/messages",
        json={"text": "send a bad one"},
    )
    assert sm.status_code == 200
    agent = sm.json()["agent_message"]
    # Proposed yaml is stripped because the schema check failed.
    assert "proposed_yaml" not in agent or not agent.get("proposed_yaml")
    # The agent's text still mentions the failed validation note.
    assert "didn't validate" in agent["text"]


def test_apply_writes_current_yaml(client: TestClient) -> None:
    r = client.post("/api/workflow-builder/sessions", json={})
    sid = r.json()["session_id"]

    a = client.post(
        f"/api/workflow-builder/sessions/{sid}/apply",
        json={"proposed_yaml": _VALID_PROPOSAL},
    )
    assert a.status_code == 200
    assert "name: my-workflow" in a.json()["current_yaml"]

    g = client.get(f"/api/workflow-builder/sessions/{sid}").json()
    assert "name: my-workflow" in g["current_yaml"]


def test_apply_rejects_invalid_yaml(client: TestClient) -> None:
    r = client.post("/api/workflow-builder/sessions", json={})
    sid = r.json()["session_id"]
    a = client.post(
        f"/api/workflow-builder/sessions/{sid}/apply",
        json={"proposed_yaml": _INVALID_PROPOSAL},
    )
    assert a.status_code == 400


def test_delete_session(client: TestClient) -> None:
    r = client.post("/api/workflow-builder/sessions", json={})
    sid = r.json()["session_id"]
    d = client.delete(f"/api/workflow-builder/sessions/{sid}")
    assert d.status_code == 200
    g = client.get(f"/api/workflow-builder/sessions/{sid}")
    assert g.status_code == 404


def test_extract_proposed_yaml_picks_last_block() -> None:
    text = "first try:\n```yaml\nname: a\nnodes: []\n```\nactually:\n```yaml workflow\nname: b\nnodes: []\n```\n"
    out = extract_proposed_yaml(text)
    assert out is not None
    assert "name: b" in out


def test_extract_proposed_yaml_returns_none_when_absent() -> None:
    assert extract_proposed_yaml("just prose, no fence") is None
    assert extract_proposed_yaml("") is None


def test_assemble_builder_prompt_includes_all_context() -> None:
    out = assemble_builder_prompt(
        builder_template="HEADER",
        current_yaml="name: x\nnodes: []",
        history=[{"from": "user", "text": "hi"}, {"from": "agent", "text": "hello"}],
        user_text="next step?",
    )
    assert "HEADER" in out
    assert "name: x" in out
    assert "user" in out and "hi" in out
    assert "agent" in out and "hello" in out
    assert "next step?" in out


def test_invalid_session_id_format_rejected(client: TestClient) -> None:
    g = client.get("/api/workflow-builder/sessions/short")
    assert g.status_code == 400
    g = client.get("/api/workflow-builder/sessions/" + "x" * 64)
    assert g.status_code == 400
