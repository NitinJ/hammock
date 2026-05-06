"""runs_if-skipped node tests — Stage 1 §1.6, filled in at Stage 6b.

A node whose ``runs_if`` predicate evaluates false renders SKIPPED via
``state.json``; the projection surfaces the state to the dashboard so
the left-pane row badge reflects it.
"""

from __future__ import annotations

import pytest

from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


@pytest.mark.asyncio
async def test_skipped_node_renders_with_state(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """A skipped node has state=skipped in the JobDetail node list."""
    fake_engine.start_job(
        workflow={
            "workflow": "T",
            "nodes": [
                {
                    "id": "optional-node",
                    "kind": "artifact",
                    "actor": "agent",
                    "runs_if": "$flag.value == true",
                }
            ],
        },
        request="x",
    )
    fake_engine.skip_node("optional-node", "runs_if false")

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    assert resp.status_code == 200, resp.text
    rows = {n["node_id"]: n for n in resp.json()["nodes"]}
    assert "optional-node" in rows
    assert rows["optional-node"]["state"] == "skipped"


@pytest.mark.asyncio
async def test_skipped_node_does_not_block_downstream(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """A skipped node + a succeeded downstream both render — the skip
    didn't wedge the projection."""
    fake_engine.start_job(
        workflow={
            "workflow": "T",
            "nodes": [
                {
                    "id": "optional-node",
                    "kind": "artifact",
                    "actor": "agent",
                    "runs_if": "$flag.value == true",
                },
                {
                    "id": "downstream",
                    "kind": "artifact",
                    "actor": "agent",
                    "after": ["optional-node"],
                },
            ],
        },
        request="x",
    )
    fake_engine.skip_node("optional-node", "runs_if false")
    fake_engine.enter_node("downstream")
    from shared.v1.types.bug_report import BugReportValue

    fake_engine.complete_node("downstream", BugReportValue(summary="ok"))

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    rows = {n["node_id"]: n for n in resp.json()["nodes"]}
    assert rows["optional-node"]["state"] == "skipped"
    assert rows["downstream"]["state"] == "succeeded"
