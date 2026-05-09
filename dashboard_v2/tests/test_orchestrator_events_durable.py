"""Regression: orchestrator events derive entirely from on-disk files.

The user reported "events vanish after page refresh." Root cause was
that rich events (Task dispatches, subagent completions) only lived in
the orchestrator's stream-json transcript and weren't surfaced on the
Events tab. Refresh reset the in-memory query cache; the events tab
re-fetched from the events endpoint, which only knew about state.md
transitions and missed the Task-derived activity.

Fix: events endpoint now also walks orchestrator.jsonl for Task
tool_use / tool_result pairs and emits subagent_dispatched /
subagent_completed events. Same input file → same output events on
refresh, by construction.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient


def _seed_completed_job_with_orchestrator_chat(root: Path, slug: str) -> None:
    """Job dir mimicking a completed run: job.md, two node state files,
    and an orchestrator.jsonl with one Task dispatch + completion."""
    job_dir = root / "jobs" / slug
    (job_dir / "nodes").mkdir(parents=True)
    (job_dir / "job.md").write_text(
        "---\n"
        f"slug: {slug}\n"
        "workflow: fix-bug\n"
        "state: completed\n"
        "submitted_at: 2026-05-09T10:00:00+00:00\n"
        "started_at: 2026-05-09T10:00:01+00:00\n"
        "finished_at: 2026-05-09T10:05:00+00:00\n"
        "---\n\n## Request\n\nx\n"
    )
    n = job_dir / "nodes" / "write-bug-report"
    n.mkdir()
    (n / "state.md").write_text(
        "---\nstate: succeeded\nstarted_at: 2026-05-09T10:00:30+00:00\n"
        "finished_at: 2026-05-09T10:01:30+00:00\n---\n"
    )
    transcript_lines = [
        {
            "type": "system",
            "subtype": "init",
            "session_id": "orchestrator-1",
            "timestamp": "2026-05-09T10:00:05+00:00",
        },
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tu-1",
                        "name": "Task",
                        "input": {
                            "description": "Run write-bug-report",
                            "subagent_type": "general-purpose",
                            "prompt": "...",
                        },
                    }
                ],
            },
            "timestamp": "2026-05-09T10:00:10+00:00",
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu-1",
                        "content": "subagent finished successfully",
                    }
                ],
            },
            "timestamp": "2026-05-09T10:01:30+00:00",
        },
    ]
    (job_dir / "orchestrator.jsonl").write_text(
        "\n".join(json.dumps(line) for line in transcript_lines) + "\n"
    )


def test_events_include_chat_derived_dispatches(client: TestClient, hammock_v2_root: Path) -> None:
    _seed_completed_job_with_orchestrator_chat(hammock_v2_root, "events-durable")
    r = client.get("/api/jobs/events-durable/orchestrator/events")
    assert r.status_code == 200
    events = r.json()["events"]
    kinds = [e["kind"] for e in events]
    assert "subagent_dispatched" in kinds, kinds
    assert "subagent_completed" in kinds, kinds
    dispatched = next(e for e in events if e["kind"] == "subagent_dispatched")
    assert dispatched["node_id"] == "write-bug-report"


def test_events_persist_across_refetch(client: TestClient, hammock_v2_root: Path) -> None:
    _seed_completed_job_with_orchestrator_chat(hammock_v2_root, "events-durable-2")
    a = client.get("/api/jobs/events-durable-2/orchestrator/events").json()["events"]
    b = client.get("/api/jobs/events-durable-2/orchestrator/events").json()["events"]
    assert a == b
    assert len(a) > 0
