"""Projection coverage for workflow_expander dynamic-merge:

- `expanded_nodes_for(slug, root)` reads orchestrator_state.json
- job_summary interleaves expanded children right after their parent
  expander, with `parent_expander` set on each child entry
- node_detail / node_chat resolve `<parent>__<child>` ids to the
  nested folder under the expander
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard_v2.api.projections import (
    expanded_nodes_for,
    resolve_node_dir,
)


def _seed_expander_job(root: Path, slug: str) -> Path:
    """Lay down a job with a workflow_expander parent + 3 expanded
    children. Returns the job_dir path."""
    job_dir = root / "jobs" / slug
    nodes_dir = job_dir / "nodes"
    nodes_dir.mkdir(parents=True)
    (job_dir / "job.md").write_text(
        "---\n"
        f"slug: {slug}\n"
        "workflow: stage-implementation\n"
        "state: running\n"
        "---\n\n## Request\n\nstaged\n"
    )
    (job_dir / "workflow.yaml").write_text(
        "name: stage-implementation\n"
        "nodes:\n"
        "  - id: read-plan\n"
        "    prompt: read-impl-plan\n"
        "  - id: execute-plan\n"
        "    kind: workflow_expander\n"
        "    after: [read-plan]\n"
        "    prompt: execute-plan-expander\n"
        "    requires: [output.md, expansion.yaml]\n"
        "  - id: write-summary\n"
        "    after: [execute-plan]\n"
        "    prompt: write-summary\n"
    )
    # Top-level node folders.
    for nid, state in (
        ("read-plan", "succeeded"),
        ("execute-plan", "succeeded"),
        ("write-summary", "pending"),
    ):
        d = nodes_dir / nid
        d.mkdir()
        (d / "state.md").write_text(f"---\nstate: {state}\n---\n")
    # Expanded children under execute-plan/.
    expander_dir = nodes_dir / "execute-plan"
    for cid, state, after in (
        ("task-a", "succeeded", []),
        ("task-b", "running", []),
        ("checkpoint", "pending", ["task-a", "task-b"]),
    ):
        cdir = expander_dir / cid
        cdir.mkdir()
        (cdir / "state.md").write_text(f"---\nstate: {state}\n---\n")
        (cdir / "chat.jsonl").write_text(
            '{"type":"system","subtype":"init"}\n'
            '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"hello from '
            + cid
            + '"}]}}\n'
        )
        del after  # captured indirectly via expanded_nodes
    # Persisted state with expanded_nodes map.
    state_json = job_dir / "orchestrator_state.json"
    state_json.write_text(
        json.dumps(
            {
                "expanded_nodes": {
                    "execute-plan__task-a": {
                        "parent_expander": "execute-plan",
                        "kind": "agent",
                        "prompt": "implement-task",
                        "after": [],
                        "human_review": False,
                        "requires": ["output.md"],
                        "worktree": True,
                        "description": None,
                    },
                    "execute-plan__task-b": {
                        "parent_expander": "execute-plan",
                        "kind": "agent",
                        "prompt": "implement-task",
                        "after": [],
                        "human_review": False,
                        "requires": ["output.md"],
                        "worktree": True,
                        "description": None,
                    },
                    "execute-plan__checkpoint": {
                        "parent_expander": "execute-plan",
                        "kind": "agent",
                        "prompt": "stage-checkpoint",
                        "after": ["execute-plan__task-a", "execute-plan__task-b"],
                        "human_review": True,
                        "requires": ["output.md"],
                        "worktree": False,
                        "description": None,
                    },
                }
            }
        )
    )
    return job_dir


def test_expanded_nodes_for_returns_persisted_map(hammock_v2_root: Path) -> None:
    _seed_expander_job(hammock_v2_root, "exp-1")
    expanded = expanded_nodes_for("exp-1", hammock_v2_root)
    assert set(expanded.keys()) == {
        "execute-plan__task-a",
        "execute-plan__task-b",
        "execute-plan__checkpoint",
    }
    assert expanded["execute-plan__task-a"]["parent_expander"] == "execute-plan"


def test_expanded_nodes_for_empty_when_state_missing(hammock_v2_root: Path) -> None:
    job_dir = hammock_v2_root / "jobs" / "no-state"
    (job_dir / "nodes").mkdir(parents=True)
    (job_dir / "job.md").write_text("---\nslug: no-state\nworkflow: x\nstate: running\n---\n")
    assert expanded_nodes_for("no-state", hammock_v2_root) == {}


def test_job_summary_interleaves_expanded_children_after_parent(
    client: TestClient, hammock_v2_root: Path
) -> None:
    _seed_expander_job(hammock_v2_root, "exp-2")
    r = client.get("/api/jobs/exp-2")
    assert r.status_code == 200
    nodes = r.json()["nodes"]
    ids = [n["id"] for n in nodes]
    # read-plan first, then execute-plan, then its 3 children, then write-summary.
    assert ids[0] == "read-plan"
    assert ids[1] == "execute-plan"
    expander_children = ids[2:5]
    # task-a and task-b have no after-edges (parallel); checkpoint waits.
    assert "execute-plan__task-a" in expander_children
    assert "execute-plan__task-b" in expander_children
    assert ids[4] == "execute-plan__checkpoint"
    assert ids[5] == "write-summary"


def test_job_summary_marks_parent_expander_on_children(
    client: TestClient, hammock_v2_root: Path
) -> None:
    _seed_expander_job(hammock_v2_root, "exp-3")
    r = client.get("/api/jobs/exp-3")
    nodes = {n["id"]: n for n in r.json()["nodes"]}
    assert nodes["execute-plan"].get("parent_expander") is None
    assert nodes["execute-plan"].get("kind") == "workflow_expander"
    for cid in (
        "execute-plan__task-a",
        "execute-plan__task-b",
        "execute-plan__checkpoint",
    ):
        assert nodes[cid]["parent_expander"] == "execute-plan"
        assert nodes[cid]["kind"] == "agent"


def test_resolve_node_dir_finds_expanded_child(hammock_v2_root: Path) -> None:
    _seed_expander_job(hammock_v2_root, "exp-4")
    folder = resolve_node_dir("exp-4", "execute-plan__task-a", hammock_v2_root)
    assert folder is not None
    assert folder.name == "task-a"
    assert folder.parent.name == "execute-plan"


def test_node_chat_endpoint_resolves_expanded_child(
    client: TestClient, hammock_v2_root: Path
) -> None:
    _seed_expander_job(hammock_v2_root, "exp-5")
    r = client.get("/api/jobs/exp-5/nodes/execute-plan__task-a/chat")
    assert r.status_code == 200
    turns = r.json()["turns"]
    assert len(turns) == 2
    assert turns[1]["type"] == "assistant"


def test_node_detail_endpoint_resolves_expanded_child(
    client: TestClient, hammock_v2_root: Path
) -> None:
    _seed_expander_job(hammock_v2_root, "exp-6")
    r = client.get("/api/jobs/exp-6/nodes/execute-plan__task-a")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "succeeded"
