"""SSE event-typing tests for `dashboard_v2.api.sse`.

The streaming endpoint is hard to drive synchronously over a
TestClient (FastAPI/anyio loops on the response in tests are messy),
so we test the path-classifier directly. The classifier covers the
full event-emission contract that the watcher uses; everything
downstream is wiring.
"""

from __future__ import annotations

import os

from dashboard_v2.api.sse import _classify


def test_classify_job_state_changed() -> None:
    assert _classify("job.md") == ("job_state_changed", None)


def test_classify_orchestrator_appended() -> None:
    assert _classify("orchestrator.jsonl") == ("orchestrator_appended", None)


def test_classify_node_state_changed() -> None:
    rel = os.path.join("nodes", "write-bug-report", "state.md")
    assert _classify(rel) == ("node_state_changed", "write-bug-report")


def test_classify_chat_appended() -> None:
    rel = os.path.join("nodes", "write-design-spec", "chat.jsonl")
    assert _classify(rel) == ("chat_appended", "write-design-spec")


def test_classify_awaiting_human() -> None:
    rel = os.path.join("nodes", "review", "awaiting_human.md")
    assert _classify(rel) == ("awaiting_human", "review")


def test_classify_human_decision_received() -> None:
    rel = os.path.join("nodes", "review", "human_decision.md")
    assert _classify(rel) == ("human_decision_received", "review")


def test_classify_unknown_files_return_none() -> None:
    assert _classify("workflow.yaml") is None
    assert _classify(os.path.join("inputs", "screenshot.png")) is None
    assert _classify(os.path.join("nodes", "x", "unknown.txt")) is None
    assert _classify("") is None


def test_classify_short_node_path_returns_none() -> None:
    # nodes/<id>/ alone — no leaf — must not crash
    assert _classify(os.path.join("nodes", "write-bug-report")) is None
