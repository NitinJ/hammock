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
    fake_engine.start_job(workflow={"schema_version": 1, "workflow": "T1"}, request="hi")
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
    fake_engine.start_job(workflow={"schema_version": 1, "workflow": "T1"}, request="x")
    resp = await dashboard.client.get("/api/jobs")
    assert resp.status_code == 200
    slugs = [j["job_slug"] for j in resp.json()]
    assert fake_engine.job_slug in slugs


@pytest.mark.asyncio
async def test_node_detail_running(dashboard: DashboardHandle, fake_engine: FakeEngine) -> None:
    fake_engine.start_job(workflow={"schema_version": 1, "workflow": "T1"}, request="x")
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

    fake_engine.start_job(workflow={"schema_version": 1, "workflow": "T1"}, request="x")
    fake_engine.enter_node("write-bug-report")
    fake_engine.complete_node(
        "write-bug-report", BugReportValue(summary="login button broken", document="## Bug\n\n.")
    )
    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    nodes = {n["node_id"]: n for n in resp.json()["nodes"]}
    assert nodes["write-bug-report"]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_node_detail_skipped_carries_reason(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    fake_engine.start_job(workflow={"schema_version": 1, "workflow": "T1"}, request="x")
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
        "schema_version": 1,
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
        BugReportValue(summary="b0", document="## Bug\n\n."),
        iter=(0,),
        loop_id="loop1",
        output_var_name="bug_report",
    )
    fake_engine.complete_node(
        "body-node",
        BugReportValue(summary="b1", document="## Bug\n\n."),
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
        "schema_version": 1,
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

    # Seed every (outer, inner) leaf execution: 2 outer iters x 2 inner
    # iters = 4 envelopes, each at its full iter_path.
    for outer_i in range(2):
        for inner_i in range(2):
            fake_engine.complete_node(
                "leaf",
                BugReportValue(summary=f"o{outer_i}i{inner_i}", document="## Bug\n\n."),
                iter=(outer_i, inner_i),
                output_var_name="bug_report",
            )

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    rows = resp.json()["nodes"]
    leaf_rows = [r for r in rows if r["node_id"] == "leaf"]
    assert [r["iter"] for r in leaf_rows] == [[0, 0], [0, 1], [1, 0], [1, 1]]


# test_node_detail_excludes_outer_loop_projection and
# test_nested_hil_pending_carries_full_iter_path were deleted in Stage C
# of loops-v2 — both were regression tests against bug classes the
# universal (node_id, iter_path) keying eliminates structurally:
#
#   - Outer-loop "3-copies" projection: v2 writes a single envelope at
#     <var>__<full-iter-token>.json plus tiny {"$ref": ...} pointer
#     files at outer scopes. node_detail filters envelopes by exact
#     (var_name, iter_token) and skips ref pointers — no heuristic.
#
#   - Nested HIL pending iter_path: v2 keys pending markers as
#     pending/<node_id>__<iter_token>.json. The projection decodes
#     iter_path from the filename — there is no single-int "iteration"
#     fallback to be wrong about.


@pytest.mark.asyncio
async def test_node_name_surfaces_in_overview(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Workflow's optional ``name:`` per-node field comes through on
    NodeListEntry; loop names come through on JobDetail.loop_names."""
    workflow = {
        "schema_version": 1,
        "workflow": "T-named",
        "variables": {
            "bug_report": {"type": "bug-report"},
            "bugs": {"type": "list[bug-report]"},
        },
        "nodes": [
            {
                "id": "named-loop",
                "name": "Named loop section",
                "kind": "loop",
                "count": 1,
                "body": [
                    {
                        "id": "named-body",
                        "name": "Named body row",
                        "kind": "artifact",
                        "actor": "agent",
                        "outputs": {"bug_report": "$bug_report"},
                    }
                ],
                "outputs": {"bugs": "$named-loop.bug_report[*]"},
            },
            {
                # No name → falls back to id on the frontend.
                "id": "unnamed-node",
                "kind": "artifact",
                "actor": "agent",
                "after": ["named-loop"],
                "outputs": {"bug_report": "$bug_report"},
            },
        ],
    }
    fake_engine.start_job(workflow=workflow, request="x")

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    body = resp.json()
    by_id = {n["node_id"]: n for n in body["nodes"]}
    assert by_id["named-body"]["name"] == "Named body row"
    assert by_id["unnamed-node"]["name"] is None
    assert body["loop_names"] == {"named-loop": "Named loop section"}
