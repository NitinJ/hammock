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

    for i in range(2):
        fake_engine.complete_node(
            "leaf",
            BugReportValue(summary=f"i{i}", document="## Bug\n\n."),
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


@pytest.mark.asyncio
async def test_node_detail_excludes_outer_loop_projection(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """A loop's output projection re-writes the body's envelope at a
    higher scope, preserving ``producer_node`` for traceability. The
    node-detail projection must filter those out — otherwise the
    body's right-pane shows the same envelope N times for an N-deep
    nested loop. Regression test for the dogfood report.

    Concretely: leaf body's envelope sits at
    ``loop_inner_bug_report_0.json``. The inner loop projects up to
    ``loop_outer_bug_report_0.json``; the outer loop projects up to
    top-level ``bug_report.json``. All three carry
    ``producer_node: leaf``. node_detail should return only the first.
    """
    from shared.v1.envelope import make_envelope
    from shared.v1.types.bug_report import BugReportValue

    workflow = {
        "schema_version": 1,
        "workflow": "T-projection-deep",
        "variables": {"bug_report": {"type": "bug-report"}},
        "nodes": [
            {
                "id": "outer",
                "kind": "loop",
                "count": 1,
                "body": [
                    {
                        "id": "inner",
                        "kind": "loop",
                        "count": 1,
                        "body": [
                            {
                                "id": "leaf",
                                "kind": "artifact",
                                "actor": "agent",
                                "outputs": {"bug_report": "$bug_report"},
                            }
                        ],
                        "outputs": {"bug_report": "$inner.bug_report[last]"},
                    }
                ],
                "outputs": {"bug_report": "$outer.bug_report[last]"},
            }
        ],
    }
    fake_engine.start_job(workflow=workflow, request="x")

    # Leaf's actual envelope under its immediate enclosing loop.
    fake_engine.complete_node(
        "leaf",
        BugReportValue(summary="from-leaf", document="## Bug\n\n."),
        iter=(0,),
        loop_id="inner",
        output_var_name="bug_report",
    )

    # Simulate the loop output projections re-writing the same envelope
    # at outer scopes. ``producer_node`` is preserved so the provenance
    # chain stays intact.
    var_dir = fake_engine.root / "jobs" / fake_engine.job_slug / "variables"
    projection = make_envelope(
        type_name="bug-report",
        producer_node="leaf",
        value_payload={
            "summary": "from-leaf",
            "repro_steps": [],
            "expected_behaviour": None,
            "actual_behaviour": None,
            "document": "## Bug\n\n.",
        },
    )
    (var_dir / "loop_outer_bug_report_0.json").write_text(projection.model_dump_json())
    (var_dir / "bug_report.json").write_text(projection.model_dump_json())

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}/nodes/leaf")
    outputs = resp.json()["outputs"]
    # Only the leaf's direct envelope (loop_inner_bug_report_0). The
    # outer-loop projection at ``loop_outer_bug_report_0`` and the
    # top-level ``bug_report`` are the same value at higher scopes —
    # they belong to the loop nodes, not the leaf.
    assert list(outputs.keys()) == ["loop_inner_bug_report_0"], outputs.keys()


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
