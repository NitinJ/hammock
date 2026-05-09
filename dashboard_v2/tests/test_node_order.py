"""Regression: JobDetail.nodes is in workflow topo order, not alphabetical.

The bug: previously job_summary listed nodes by `sorted(nodes_dir.iterdir())`,
which renders as alphabetical-by-id. The user-visible symptom is the
fix-bug timeline showing `implement` before `write-bug-report` etc.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _seed_job_with_workflow_snapshot(root: Path, slug: str) -> None:
    """Lay down a job dir + workflow.yaml snapshot mirroring fix-bug's
    ordering, plus a few state.md files in some non-topo order."""
    job_dir = root / "jobs" / slug
    job_dir.mkdir(parents=True)
    nodes_dir = job_dir / "nodes"
    nodes_dir.mkdir()
    (job_dir / "job.md").write_text(
        "---\n"
        f"slug: {slug}\n"
        "workflow: fix-bug\n"
        "state: running\n"
        "submitted_at: 2026-05-09T10:00:00+00:00\n"
        "---\n\n## Request\n\nx\n"
    )
    # Synthetic workflow snapshot: A → B → C, but write nodes folders
    # in reverse on disk so alphabetical and topo would diverge.
    (job_dir / "workflow.yaml").write_text(
        "name: ordered-test\n"
        "nodes:\n"
        "  - id: zebra\n"
        "    prompt: write-bug-report\n"
        "  - id: middle\n"
        "    prompt: write-bug-report\n"
        "    after: [zebra]\n"
        "  - id: alpha\n"
        "    prompt: write-bug-report\n"
        "    after: [middle]\n"
    )
    for nid in ("alpha", "middle", "zebra"):
        n = nodes_dir / nid
        n.mkdir()
        (n / "state.md").write_text("---\nstate: pending\n---\n")


def test_job_summary_orders_nodes_topologically(client: TestClient, hammock_v2_root: Path) -> None:
    _seed_job_with_workflow_snapshot(hammock_v2_root, "ordered-test-job")
    r = client.get("/api/jobs/ordered-test-job")
    assert r.status_code == 200
    ids = [n["id"] for n in r.json()["nodes"]]
    assert ids == ["zebra", "middle", "alpha"], f"expected topo order zebra→middle→alpha, got {ids}"


def test_job_summary_falls_back_to_alphabetical_when_workflow_missing(
    client: TestClient, hammock_v2_root: Path
) -> None:
    """Without workflow.yaml snapshot, fall back to alphabetical."""
    job_dir = hammock_v2_root / "jobs" / "no-workflow"
    job_dir.mkdir(parents=True)
    (job_dir / "nodes").mkdir()
    (job_dir / "job.md").write_text(
        "---\nslug: no-workflow\nworkflow: x\nstate: running\n---\n\n## Request\n\nx\n"
    )
    for nid in ("c-node", "a-node", "b-node"):
        n = job_dir / "nodes" / nid
        n.mkdir()
        (n / "state.md").write_text("---\nstate: pending\n---\n")
    r = client.get("/api/jobs/no-workflow")
    assert r.status_code == 200
    ids = [n["id"] for n in r.json()["nodes"]]
    assert ids == ["a-node", "b-node", "c-node"]
