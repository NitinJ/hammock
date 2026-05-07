"""Loop iteration unrolling — Stage 1 §1.6, filled in at Stage 6a.

Drives a workflow with a loop body via FakeEngine, then asserts the
``GET /api/jobs/{slug}`` projection emits one row per (body_node,
iteration) tagged with ``iter`` + ``parent_loop_id``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from shared.v1 import paths as v1_paths
from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine


class _Bug(BaseModel):
    summary: str


def _outer_count_workflow(count: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workflow": "t-loop-unroll",
        "variables": {
            "request": {"type": "job-request"},
            "bug_report": {"type": "bug-report"},
            "bugs": {"type": "list[bug-report]"},
        },
        "nodes": [
            {
                "id": "outer",
                "kind": "loop",
                "count": count,
                "body": [
                    {
                        "id": "write-one",
                        "kind": "artifact",
                        "actor": "agent",
                        "outputs": {"bug_report": "$bug_report"},
                    }
                ],
                "outputs": {"bugs": "$outer.bug_report[*]"},
            }
        ],
    }


def _nested_workflow() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "workflow": "t-nested-unroll",
        "variables": {
            "request": {"type": "job-request"},
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
                                "id": "write-one",
                                "kind": "artifact",
                                "actor": "agent",
                                "outputs": {"bug_report": "$bug_report"},
                            }
                        ],
                        "outputs": {"bugs": "$inner.bug_report[*]"},
                    }
                ],
                "outputs": {"all_bugs": "$outer.bugs[last]"},
            }
        ],
    }


def _seed_loop_body_envelope(
    fake_engine: FakeEngine, *, loop_id: str, var_name: str, iteration: int, summary: str
) -> None:
    """Write a body-produced envelope at the per-iteration loop path."""
    fake_engine.complete_node(
        "write-one",
        _Bug(summary=summary),
        iter=(iteration,),
        loop_id=loop_id,
        output_var_name=var_name,
        type_name="bug-report",
    )


@pytest.mark.asyncio
async def test_outer_loop_three_iterations_unroll(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """A count-loop with body that has run 3 times produces 3 rows for
    the body node, each tagged with iter=[i] and parent_loop_id=outer."""
    fake_engine.start_job(workflow=_outer_count_workflow(3), request="x")
    for i in range(3):
        _seed_loop_body_envelope(
            fake_engine, loop_id="outer", var_name="bug_report", iteration=i, summary=f"bug{i}"
        )

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    assert resp.status_code == 200, resp.text
    nodes = resp.json()["nodes"]

    write_rows = [n for n in nodes if n["node_id"] == "write-one"]
    assert len(write_rows) == 3
    assert [n["iter"] for n in write_rows] == [[0], [1], [2]]
    assert all(n["parent_loop_id"] == "outer" for n in write_rows)
    assert all(n["state"] == "succeeded" for n in write_rows)
    # The loop node itself is not surfaced as a row.
    assert all(n["node_id"] != "outer" for n in nodes)


@pytest.mark.asyncio
async def test_nested_loop_two_levels_unroll(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Inner-loop body envelopes drive inner iteration count; outer
    envelopes drive outer count. Body rows nest with iter=[outer, inner]."""
    fake_engine.start_job(workflow=_nested_workflow(), request="x")
    # Inner loop has run 2 iters at the latest outer pass.
    for i in range(2):
        _seed_loop_body_envelope(
            fake_engine, loop_id="inner", var_name="bug_report", iteration=i, summary=f"i{i}"
        )
    # Outer loop has run 2 iters (its projected output `bugs` exists per outer iter).
    var_dir = v1_paths.variables_dir(fake_engine.job_slug, root=fake_engine.root)
    for outer_i in range(2):
        v1_paths.loop_variable_envelope_path(
            fake_engine.job_slug,
            "outer",
            "bugs",
            outer_i,
            root=fake_engine.root,
        ).parent.mkdir(parents=True, exist_ok=True)
        (var_dir / f"loop_outer_bugs_{outer_i}.json").write_text(
            '{"type":"list[bug-report]","version":"1","repo":null,'
            '"producer_node":"<loop:inner>","produced_at":"2026-05-06T00:00:00",'
            '"value":[]}'
        )

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    assert resp.status_code == 200, resp.text
    nodes = resp.json()["nodes"]

    write_rows = [n for n in nodes if n["node_id"] == "write-one"]
    # 2 outer iters x 2 inner iters = 4 rows.
    assert len(write_rows) == 4
    iters = [n["iter"] for n in write_rows]
    assert iters == [[0, 0], [0, 1], [1, 0], [1, 1]]
    assert all(n["parent_loop_id"] == "inner" for n in write_rows)


@pytest.mark.asyncio
async def test_loop_indexed_envelopes_resolve_per_iteration(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Each per-iteration body envelope drives its own row's success
    state; an iteration whose body has not produced yet stays pending."""
    fake_engine.start_job(workflow=_outer_count_workflow(3), request="x")
    # Only iter 0 + 1 have produced; iter 2 hasn't yet.
    for i in range(2):
        _seed_loop_body_envelope(
            fake_engine, loop_id="outer", var_name="bug_report", iteration=i, summary=f"bug{i}"
        )
    fake_engine.enter_node("write-one")  # state.json: running, attempts=1

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    nodes = resp.json()["nodes"]
    rows = {tuple(n["iter"]): n for n in nodes if n["node_id"] == "write-one"}
    # 2 iterations seen on disk → 2 rows. iter 2 is not yet visible.
    assert set(rows.keys()) == {(0,), (1,)}
    assert rows[(0,)]["state"] == "succeeded"
    # iter 1 is the latest seen; envelope present, so still succeeded.
    assert rows[(1,)]["state"] == "succeeded"


# ---------------------------------------------------------------------------
# loop-rendering-fixes — projection-side contract changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_with_no_envelopes_still_shows_iter_zero_pending(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """A loop that hasn't produced any output yet must still surface
    its iter-0 body rows so the operator sees what's about to run.

    Without this, a code-kind loop that's currently dispatching but
    hasn't written its first envelope is invisible — the user only
    sees the rows after the first iteration succeeds (Issue 1)."""
    fake_engine.start_job(workflow=_outer_count_workflow(3), request="x")
    # No envelopes seeded — the loop is conceptually "just about to run".

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    rows = resp.json()["nodes"]
    write_rows = [n for n in rows if n["node_id"] == "write-one"]
    assert len(write_rows) == 1
    assert write_rows[0]["iter"] == [0]
    assert write_rows[0]["state"] == "pending"


@pytest.mark.asyncio
async def test_node_carries_loop_path_parallel_to_iter(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Each row carries ``loop_path: list[str]`` parallel to ``iter`` so
    the frontend can distinguish sibling loops whose iterations happen
    to be the same indices.

    Without this, two sibling top-level loops both emit body rows tagged
    ``iter=[0,0]`` and the renderer can't tell which loop they belong
    to (Issue 4)."""
    fake_engine.start_job(workflow=_nested_workflow(), request="x")

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    rows = resp.json()["nodes"]
    write_rows = [n for n in rows if n["node_id"] == "write-one"]
    assert len(write_rows) >= 1
    for r in write_rows:
        # loop_path is parallel to iter — same length, one loop_id per
        # nesting level.
        assert "loop_path" in r, f"expected loop_path on row, got: {r}"
        assert len(r["loop_path"]) == len(r["iter"])
        # Innermost loop is "inner".
        assert r["loop_path"][-1] == "inner"
        # Outermost loop is "outer".
        assert r["loop_path"][0] == "outer"


@pytest.mark.asyncio
async def test_sibling_loops_have_distinct_loop_paths(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Two sibling top-level loops both produce body rows at iter [0],
    but their loop_path differs so the renderer can break the section
    header per loop."""
    workflow = {
        "schema_version": 1,
        "workflow": "T-siblings",
        "variables": {
            "bug_report": {"type": "bug-report"},
            "bugs": {"type": "list[bug-report]"},
        },
        "nodes": [
            {
                "id": "loop-a",
                "kind": "loop",
                "count": 1,
                "body": [
                    {
                        "id": "body-a",
                        "kind": "artifact",
                        "actor": "agent",
                        "outputs": {"bug_report": "$bug_report"},
                    }
                ],
                "outputs": {"bugs": "$loop-a.bug_report[*]"},
            },
            {
                "id": "loop-b",
                "kind": "loop",
                "count": 1,
                "body": [
                    {
                        "id": "body-b",
                        "kind": "artifact",
                        "actor": "agent",
                        "outputs": {"bug_report": "$bug_report"},
                    }
                ],
                "outputs": {"bugs": "$loop-b.bug_report[*]"},
            },
        ],
    }
    fake_engine.start_job(workflow=workflow, request="x")

    resp = await dashboard.client.get(f"/api/jobs/{fake_engine.job_slug}")
    rows = resp.json()["nodes"]
    a_rows = [n for n in rows if n["node_id"] == "body-a"]
    b_rows = [n for n in rows if n["node_id"] == "body-b"]
    assert len(a_rows) == 1
    assert len(b_rows) == 1
    # Same iter index from sibling loops, but distinct loop_paths.
    assert a_rows[0]["iter"] == [0]
    assert b_rows[0]["iter"] == [0]
    assert a_rows[0]["loop_path"] == ["loop-a"]
    assert b_rows[0]["loop_path"] == ["loop-b"]
