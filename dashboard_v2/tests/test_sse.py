"""SSE event-typing tests for `dashboard_v2.api.sse`.

The streaming endpoint is hard to drive synchronously over a
TestClient (FastAPI/anyio loops on the response in tests are messy),
so we test the path-classifier directly. The classifier covers the
full event-emission contract that the watcher uses; everything
downstream is wiring.
"""

from __future__ import annotations

import os

from dashboard_v2.api.sse import classify


def testclassify_job_state_changed() -> None:
    assert classify("job.md") == ("job_state_changed", None)


def testclassify_orchestrator_appended() -> None:
    assert classify("orchestrator.jsonl") == ("orchestrator_appended", None)


def testclassify_node_state_changed() -> None:
    rel = os.path.join("nodes", "write-bug-report", "state.md")
    assert classify(rel) == ("node_state_changed", "write-bug-report")


def testclassify_chat_appended() -> None:
    rel = os.path.join("nodes", "write-design-spec", "chat.jsonl")
    assert classify(rel) == ("chat_appended", "write-design-spec")


def testclassify_awaiting_human() -> None:
    rel = os.path.join("nodes", "review", "awaiting_human.md")
    assert classify(rel) == ("awaiting_human", "review")


def testclassify_human_decision_received() -> None:
    rel = os.path.join("nodes", "review", "human_decision.md")
    assert classify(rel) == ("human_decision_received", "review")


def testclassify_unknown_files_return_none() -> None:
    assert classify("workflow.yaml") is None
    assert classify(os.path.join("inputs", "screenshot.png")) is None
    assert classify(os.path.join("nodes", "x", "unknown.txt")) is None
    assert classify("") is None


def testclassify_short_node_path_returns_none() -> None:
    # nodes/<id>/ alone — no leaf — must not crash
    assert classify(os.path.join("nodes", "write-bug-report")) is None
