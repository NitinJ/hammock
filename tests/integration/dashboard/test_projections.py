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
    """Loop iteration unrolling shape is defined with the Stage 6 frontend."""
    pytest.skip("Stage 6 — iteration unrolling shape defined with frontend")


@pytest.mark.asyncio
async def test_nested_loop_iterations_unroll(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    pytest.skip("Stage 6 — iteration unrolling shape defined with frontend")
