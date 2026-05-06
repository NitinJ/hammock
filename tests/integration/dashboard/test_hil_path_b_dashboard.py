"""HIL Path B — implicit (agent-initiated) HIL via ``ask_human`` MCP.

Per design-patch §9.5 / Stage 6a: implicit asks are surfaced through
the same ``GET /api/hil`` endpoint as explicit HIL gates, tagged
``kind: "implicit"`` so the frontend dispatches to ``AskHumanDisplay``.
The submission path is separate (``POST .../asks/{call_id}/answer``)
because the marker shape and target file are different.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from shared.atomic import atomic_write_text
from shared.v1 import paths as v1_paths
from shared.v1.job import make_job_config
from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


def _seed_job(fake_engine: FakeEngine) -> None:
    """Lay down a minimal v1 job so /api/hil knows the workflow_name."""
    workflow_path = v1_paths.job_dir(fake_engine.job_slug, root=fake_engine.root) / "workflow.yaml"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text("workflow: t-implicit-hil\nnodes: []\n")
    v1_paths.ensure_job_layout(fake_engine.job_slug, root=fake_engine.root)
    cfg = make_job_config(
        job_slug=fake_engine.job_slug,
        workflow_name="t-implicit-hil",
        workflow_path=workflow_path,
        repo_slug=None,
    )
    atomic_write_text(
        v1_paths.job_config_path(fake_engine.job_slug, root=fake_engine.root),
        cfg.model_dump_json(),
    )


def _write_ask_marker(
    fake_engine: FakeEngine,
    *,
    call_id: str,
    node_id: str,
    question: str,
    iter_str: str | None = None,
) -> None:
    asks_dir = v1_paths.job_dir(fake_engine.job_slug, root=fake_engine.root) / "asks"
    asks_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "question": question,
        "node_id": node_id,
        "iter": iter_str,
        "created_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_text(asks_dir / f"{call_id}.json", json.dumps(payload, indent=2))


@pytest.mark.asyncio
async def test_implicit_hil_marker_visible_in_api(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """An ``asks/<call_id>.json`` marker appears in /api/hil tagged
    ``kind=implicit`` with the question + node scope preserved."""
    _seed_job(fake_engine)
    _write_ask_marker(
        fake_engine,
        call_id="ask_2026_my-node_abc123",
        node_id="my-node",
        question="Should I do it?",
        iter_str="0,1",
    )

    resp = await dashboard.client.get(f"/api/hil/{fake_engine.job_slug}")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "implicit"
    assert item["call_id"] == "ask_2026_my-node_abc123"
    assert item["question"] == "Should I do it?"
    assert item["node_id"] == "my-node"
    assert item["iter"] == [0, 1]
    assert item["workflow_name"] == "t-implicit-hil"


@pytest.mark.asyncio
async def test_implicit_hil_answer_modifies_marker(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Submitting via POST .../asks/{call_id}/answer rewrites the marker
    to add an ``answer`` field — that's how the MCP server unblocks."""
    _seed_job(fake_engine)
    _write_ask_marker(
        fake_engine,
        call_id="ask_2026_n_aa",
        node_id="n",
        question="?",
    )

    resp = await dashboard.client.post(
        f"/api/hil/{fake_engine.job_slug}/asks/ask_2026_n_aa/answer",
        json={"answer": "yes do it"},
    )
    assert resp.status_code == 200, resp.text

    marker = (
        v1_paths.job_dir(fake_engine.job_slug, root=fake_engine.root)
        / "asks"
        / "ask_2026_n_aa.json"
    )
    assert marker.is_file()
    data = json.loads(marker.read_text())
    assert data["answer"] == "yes do it"
    # Original fields preserved.
    assert data["question"] == "?"
    assert data["node_id"] == "n"

    # Listing now omits the answered marker.
    resp2 = await dashboard.client.get(f"/api/hil/{fake_engine.job_slug}")
    assert resp2.json() == []


@pytest.mark.asyncio
async def test_explicit_and_implicit_share_one_inbox(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """``GET /api/hil`` returns both kinds in one list, each carrying
    workflow_name + job_slug so the frontend can identify origin."""
    _seed_job(fake_engine)
    fake_engine.request_hil("review-spec-human", "review-verdict", output_var_names=["review"])
    _write_ask_marker(fake_engine, call_id="ask_x", node_id="some-node", question="?")

    resp = await dashboard.client.get("/api/hil")
    items = resp.json()
    kinds = sorted(i["kind"] for i in items)
    assert kinds == ["explicit", "implicit"]
    for i in items:
        assert i["job_slug"] == fake_engine.job_slug
        assert i["workflow_name"] == "t-implicit-hil"
