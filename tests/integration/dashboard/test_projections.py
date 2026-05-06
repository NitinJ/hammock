"""Projection tests — Stage 3 fills in Stage 1 §1.6 stubs.

Given scripted disk state via FakeEngine, the dashboard's HTTP
endpoints return the expected JSON shape.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


@pytest.mark.asyncio
async def test_job_overview_after_start(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    fake_engine.start_job(workflow={"workflow": "T1"}, request="hi")
    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_slug"] == fake_engine.job_slug
    assert data["workflow_name"] == "T1"
    assert data["state"] == "submitted"
    assert isinstance(data["nodes"], list)


@pytest.mark.asyncio
async def test_job_list_returns_started_jobs(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    fake_engine.start_job(workflow={"workflow": "T1"}, request="x")
    resp = await dashboard.client.get("/api/jobs")
    assert resp.status_code == 200
    slugs = [j["job_slug"] for j in resp.json()]
    assert fake_engine.job_slug in slugs


@pytest.mark.asyncio
async def test_node_detail_running(dashboard: DashboardHandle, fake_engine: FakeEngine) -> None:
    fake_engine.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine.enter_node("write-bug-report")
    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    assert resp.status_code == 200
    nodes = {n["node_id"]: n for n in resp.json()["nodes"]}
    assert "write-bug-report" in nodes
    assert nodes["write-bug-report"]["state"] == "running"
    assert nodes["write-bug-report"]["attempts"] == 1


@pytest.mark.asyncio
async def test_node_detail_succeeded_with_envelope(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    from shared.v1.types.bug_report import BugReportValue

    fake_engine.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine.enter_node("write-bug-report")
    fake_engine.complete_node("write-bug-report", BugReportValue(summary="login button broken"))
    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    nodes = {n["node_id"]: n for n in resp.json()["nodes"]}
    assert nodes["write-bug-report"]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_node_detail_skipped_carries_reason(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    fake_engine.start_job(workflow={"workflow": "T1"}, request="x")
    fake_engine.skip_node("optional-node", "runs_if false")
    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    nodes = {n["node_id"]: n for n in resp.json()["nodes"]}
    assert nodes["optional-node"]["state"] == "skipped"


@pytest.mark.asyncio
async def test_loop_iterations_unroll_in_overview(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Spot-check that JobDetail.nodes carries iter[]+parent_loop_id for
    body rows. Detailed coverage lives in test_loop_unroll.py."""
    workflow = {
        "workflow": "T-unroll",
        "variables": {
            "bug_report": {"type": "bug-report"},
            "bugs": {"type": "list[bug-report]"},
        },
        "nodes": [
            {
                "id": "loop1",
                "kind": "loop",
                "count": 2,
                "body": [
                    {
                        "id": "body-node",
                        "kind": "artifact",
                        "actor": "agent",
                        "outputs": {"bug_report": "$bug_report"},
                    }
                ],
                "outputs": {"bugs": "$loop1.bug_report[*]"},
            }
        ],
    }
    fake_engine.start_job(workflow=workflow, request="x")
    from shared.v1.types.bug_report import BugReportValue

    fake_engine.complete_node(
        "body-node",
        BugReportValue(summary="b0"),
        iter=(0,),
        loop_id="loop1",
        output_var_name="bug_report",
    )
    fake_engine.complete_node(
        "body-node",
        BugReportValue(summary="b1"),
        iter=(1,),
        loop_id="loop1",
        output_var_name="bug_report",
    )

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    rows = resp.json()["nodes"]
    body_rows = [r for r in rows if r["node_id"] == "body-node"]
    assert [r["iter"] for r in body_rows] == [[0], [1]]
    assert all(r["parent_loop_id"] == "loop1" for r in body_rows)


@pytest.mark.asyncio
async def test_nested_loop_iterations_unroll(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Nested loop bodies emit rows with iter=[outer, inner]."""
    workflow = {
        "workflow": "T-nested",
        "variables": {
            "bug_report": {"type": "bug-report"},
            "bugs": {"type": "list[bug-report]"},
        },
        "nodes": [
            {
                "id": "outer",
                "kind": "loop",
                "count": 2,
                "body": [
                    {
                        "id": "inner",
                        "kind": "loop",
                        "count": 2,
                        "body": [
                            {
                                "id": "leaf",
                                "kind": "artifact",
                                "actor": "agent",
                                "outputs": {"bug_report": "$bug_report"},
                            }
                        ],
                        "outputs": {"bugs": "$inner.bug_report[*]"},
                    }
                ],
                "outputs": {"all": "$outer.bugs[last]"},
            }
        ],
    }
    fake_engine.start_job(workflow=workflow, request="x")
    from shared.v1.types.bug_report import BugReportValue

    for i in range(2):
        fake_engine.complete_node(
            "leaf",
            BugReportValue(summary=f"i{i}"),
            iter=(i,),
            loop_id="inner",
            output_var_name="bug_report",
        )
    var_dir = fake_engine.root / "jobs" / fake_engine.job_slug / "variables"
    for outer_i in range(2):
        (var_dir / f"loop_outer_bugs_{outer_i}.json").write_text(
            '{"type":"list[bug-report]","version":"1","repo":null,'
            '"producer_node":"<loop:inner>","produced_at":"2026-05-06T00:00:00",'
            '"value":[]}'
        )

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    rows = resp.json()["nodes"]
    leaf_rows = [r for r in rows if r["node_id"] == "leaf"]
    assert [r["iter"] for r in leaf_rows] == [[0, 0], [0, 1], [1, 0], [1, 1]]
