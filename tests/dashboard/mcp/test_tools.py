"""Unit tests for the four MCP tools — direct (non-wire) invocation."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from dashboard.mcp.server import (
    MCPToolError,
    append_stages,
    open_ask,
    open_task,
    update_task,
)
from shared.models.hil import HilItem
from shared.models.task import TaskRecord, TaskState
from shared.paths import (
    hil_item_path,
    job_stage_list,
    task_dir,
    task_json,
)

# Helpers --------------------------------------------------------------------


JOB = "proj-feat"
STAGE = "implement-1"


def _write_hil_answer(
    item_path: Path, answer: dict[str, object], *, status: str = "answered"
) -> None:
    """Simulate the dashboard's HIL submit by writing the answer back."""
    payload = json.loads(item_path.read_text())
    payload["status"] = status
    payload["answer"] = answer
    payload["answered_at"] = datetime.now(tz=UTC).isoformat()
    item_path.write_text(json.dumps(payload))


# open_task ------------------------------------------------------------------


async def test_open_task_writes_task_json(hammock_root: Path) -> None:
    res = await open_task(
        job_slug=JOB,
        stage_id=STAGE,
        task_spec="rewrite the auth module",
        worktree_branch="job/proj-feat/stage/implement-1/task/refactor",
        root=hammock_root,
    )
    assert "task_id" in res
    task_id = res["task_id"]

    path = task_json(JOB, STAGE, task_id, root=hammock_root)
    assert path.exists()
    record = TaskRecord.model_validate_json(path.read_text())
    assert record.task_id == task_id
    assert record.stage_id == STAGE
    assert record.state is TaskState.RUNNING
    assert record.branch == "job/proj-feat/stage/implement-1/task/refactor"
    # task-spec.md is also written so the agent can re-read it
    spec_path = task_dir(JOB, STAGE, task_id, root=hammock_root) / "task-spec.md"
    assert spec_path.exists()
    assert "rewrite the auth module" in spec_path.read_text()


async def test_open_task_ids_are_unique(hammock_root: Path) -> None:
    ids = set()
    for _ in range(5):
        res = await open_task(
            job_slug=JOB,
            stage_id=STAGE,
            task_spec="x",
            worktree_branch="b",
            root=hammock_root,
        )
        ids.add(res["task_id"])
    assert len(ids) == 5


# update_task ----------------------------------------------------------------


async def test_update_task_transitions_state(hammock_root: Path) -> None:
    res = await open_task(
        job_slug=JOB, stage_id=STAGE, task_spec="x", worktree_branch="b", root=hammock_root
    )
    task_id = res["task_id"]

    upd = await update_task(
        job_slug=JOB,
        stage_id=STAGE,
        task_id=task_id,
        status="DONE",
        result={"output_files": ["src/auth.py"], "ok": True},
        root=hammock_root,
    )
    assert upd == {"ok": True}

    record = TaskRecord.model_validate_json(
        task_json(JOB, STAGE, task_id, root=hammock_root).read_text()
    )
    assert record.state is TaskState.DONE
    assert record.ended_at is not None

    # Result is persisted as a sidecar (task-result.json)
    result_path = task_dir(JOB, STAGE, task_id, root=hammock_root) / "task-result.json"
    assert result_path.exists()
    assert json.loads(result_path.read_text())["output_files"] == ["src/auth.py"]


async def test_update_task_unknown_id_errors(hammock_root: Path) -> None:
    with pytest.raises(MCPToolError, match="not found"):
        await update_task(
            job_slug=JOB,
            stage_id=STAGE,
            task_id="task-does-not-exist",
            status="DONE",
            root=hammock_root,
        )


async def test_update_task_invalid_state_errors(hammock_root: Path) -> None:
    res = await open_task(
        job_slug=JOB, stage_id=STAGE, task_spec="x", worktree_branch="b", root=hammock_root
    )
    with pytest.raises(MCPToolError, match="invalid status"):
        await update_task(
            job_slug=JOB,
            stage_id=STAGE,
            task_id=res["task_id"],
            status="ALMOST_DONE",
            root=hammock_root,
        )


# open_ask -------------------------------------------------------------------


async def test_open_ask_blocks_until_answered(hammock_root: Path) -> None:
    """``open_ask`` long-polls until the HIL item file gets an answer."""

    async def _answer_after_delay() -> None:
        # Wait for the awaiting item to appear, then write the answer.
        from shared.paths import hil_dir

        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            items = list(hil_dir(JOB, root=hammock_root).glob("*.json"))
            if items:
                _write_hil_answer(
                    items[0], {"kind": "ask", "text": "use the new flow", "choice": None}
                )
                return
            await asyncio.sleep(0.02)
        raise AssertionError("timed out waiting for HIL item to be created")

    answer_task = asyncio.create_task(_answer_after_delay())
    answer = await open_ask(
        job_slug=JOB,
        stage_id=STAGE,
        kind="ask",
        text="What flow should I use?",
        root=hammock_root,
        poll_interval=0.02,
        timeout=2.5,
    )
    await answer_task
    assert answer["kind"] == "ask"
    assert answer["text"] == "use the new flow"


async def test_open_ask_writes_awaiting_then_returns(hammock_root: Path) -> None:
    """Item is ``awaiting`` while we block; ``answered`` after we return."""

    captured: dict[str, Path] = {}

    async def _answer_after_check() -> None:
        from shared.paths import hil_dir

        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            items = list(hil_dir(JOB, root=hammock_root).glob("*.json"))
            if items:
                captured["item_path"] = items[0]
                # Verify the item was written ``awaiting``
                hil = HilItem.model_validate_json(items[0].read_text())
                assert hil.status == "awaiting"
                assert hil.kind == "review"
                _write_hil_answer(
                    items[0],
                    {"kind": "review", "decision": "approve", "comments": "lgtm"},
                )
                return
            await asyncio.sleep(0.02)

    bg = asyncio.create_task(_answer_after_check())
    ans = await open_ask(
        job_slug=JOB,
        stage_id=STAGE,
        kind="review",
        target="spec.md",
        prompt="Approve?",
        root=hammock_root,
        poll_interval=0.02,
        timeout=2.5,
    )
    await bg

    assert ans == {"kind": "review", "decision": "approve", "comments": "lgtm"}
    final = HilItem.model_validate_json(captured["item_path"].read_text())
    assert final.status == "answered"
    assert final.answered_at is not None


async def test_open_ask_cancelled_raises(hammock_root: Path) -> None:
    async def _cancel_after_delay() -> None:
        from shared.paths import hil_dir

        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            items = list(hil_dir(JOB, root=hammock_root).glob("*.json"))
            if items:
                payload = json.loads(items[0].read_text())
                payload["status"] = "cancelled"
                items[0].write_text(json.dumps(payload))
                return
            await asyncio.sleep(0.02)

    bg = asyncio.create_task(_cancel_after_delay())
    with pytest.raises(MCPToolError, match="cancelled"):
        await open_ask(
            job_slug=JOB,
            stage_id=STAGE,
            kind="ask",
            text="anything?",
            root=hammock_root,
            poll_interval=0.02,
            timeout=2.5,
        )
    await bg


async def test_open_ask_timeout(hammock_root: Path) -> None:
    """``timeout`` raises if no answer appears within the budget."""
    with pytest.raises(MCPToolError, match="timeout"):
        await open_ask(
            job_slug=JOB,
            stage_id=STAGE,
            kind="ask",
            text="anything?",
            root=hammock_root,
            poll_interval=0.02,
            timeout=0.1,
        )

    # The pending HIL item is left ``awaiting``; the orphan sweeper (Stage 7)
    # is responsible for cancelling it on stage restart.
    items = list(hil_item_path(JOB, "x", root=hammock_root).parent.glob("*.json"))
    assert len(items) == 1
    hil = HilItem.model_validate_json(items[0].read_text())
    assert hil.status == "awaiting"


async def test_open_ask_kinds_validate_fields(hammock_root: Path) -> None:
    """Each kind has its own required fields; missing ones raise."""
    with pytest.raises(MCPToolError, match="text"):
        await open_ask(
            job_slug=JOB,
            stage_id=STAGE,
            kind="ask",
            root=hammock_root,
            poll_interval=0.02,
            timeout=0.05,
        )

    with pytest.raises(MCPToolError, match="manual-step"):
        await open_ask(
            job_slug=JOB,
            stage_id=STAGE,
            kind="manual-step",
            root=hammock_root,
            poll_interval=0.02,
            timeout=0.05,
        )


async def test_open_ask_concurrent_distinct_items(hammock_root: Path) -> None:
    """Two concurrent open_ask calls produce two distinct HIL items."""

    async def _race() -> None:
        from shared.paths import hil_dir

        deadline = asyncio.get_event_loop().time() + 2.0
        seen: set[str] = set()
        while asyncio.get_event_loop().time() < deadline:
            for p in hil_dir(JOB, root=hammock_root).glob("*.json"):
                if p.name in seen:
                    continue
                payload = json.loads(p.read_text())
                if payload["status"] != "awaiting":
                    continue
                seen.add(p.name)
                _write_hil_answer(
                    p,
                    {"kind": "ask", "text": f"answer-{len(seen)}", "choice": None},
                )
            if len(seen) >= 2:
                return
            await asyncio.sleep(0.02)

    bg = asyncio.create_task(_race())
    a, b = await asyncio.gather(
        open_ask(
            job_slug=JOB,
            stage_id=STAGE,
            kind="ask",
            text="q1",
            root=hammock_root,
            poll_interval=0.02,
            timeout=2.5,
        ),
        open_ask(
            job_slug=JOB,
            stage_id=STAGE,
            kind="ask",
            text="q2",
            root=hammock_root,
            poll_interval=0.02,
            timeout=2.5,
        ),
    )
    await bg

    assert {a["text"], b["text"]} == {"answer-1", "answer-2"}


# append_stages --------------------------------------------------------------


async def test_append_stages_creates_file_and_appends(hammock_root: Path) -> None:
    stages_path = job_stage_list(JOB, root=hammock_root)
    stages_path.parent.mkdir(parents=True, exist_ok=True)
    # Pre-existing file (the compiler wrote initial stages)
    stages_path.write_text(
        yaml.safe_dump(
            {
                "stages": [
                    {
                        "id": "spec",
                        "worker": "agent",
                        "inputs": {"required": [], "optional": None},
                        "outputs": {"required": ["spec.md"]},
                    }
                ]
            }
        )
    )

    res = await append_stages(
        job_slug=JOB,
        stages=[
            {
                "id": "implement-1",
                "worker": "agent",
                "inputs": {"required": ["spec.md"], "optional": None},
                "outputs": {"required": ["src/feature.py"]},
            },
            {
                "id": "implement-2",
                "worker": "agent",
                "inputs": {"required": ["spec.md"], "optional": None},
                "outputs": {"required": ["src/feature_b.py"]},
            },
        ],
        root=hammock_root,
    )
    assert res == {"ok": True, "count": 2}

    data = yaml.safe_load(stages_path.read_text())
    ids = [s["id"] for s in data["stages"]]
    assert ids == ["spec", "implement-1", "implement-2"]


async def test_append_stages_rejects_duplicate_id(hammock_root: Path) -> None:
    stages_path = job_stage_list(JOB, root=hammock_root)
    stages_path.parent.mkdir(parents=True, exist_ok=True)
    stages_path.write_text(yaml.safe_dump({"stages": [{"id": "spec", "worker": "agent"}]}))

    with pytest.raises(MCPToolError, match="duplicate"):
        await append_stages(
            job_slug=JOB,
            stages=[{"id": "spec", "worker": "agent"}],
            root=hammock_root,
        )


async def test_append_stages_creates_file_when_missing(hammock_root: Path) -> None:
    res = await append_stages(
        job_slug=JOB,
        stages=[{"id": "first", "worker": "agent"}],
        root=hammock_root,
    )
    assert res == {"ok": True, "count": 1}
    data = yaml.safe_load(job_stage_list(JOB, root=hammock_root).read_text())
    assert data["stages"][0]["id"] == "first"


# Path-injection guards ------------------------------------------------------


@pytest.mark.parametrize("bad", ["../escape", "..", "a/b", "x\x00y", "", "-leading", ".hidden"])
async def test_open_task_rejects_bad_slugs(hammock_root: Path, bad: str) -> None:
    with pytest.raises(MCPToolError, match="invalid"):
        await open_task(
            job_slug=bad,
            stage_id=STAGE,
            task_spec="x",
            worktree_branch="b",
            root=hammock_root,
        )
    with pytest.raises(MCPToolError, match="invalid"):
        await open_task(
            job_slug=JOB,
            stage_id=bad,
            task_spec="x",
            worktree_branch="b",
            root=hammock_root,
        )


async def test_update_task_rejects_path_traversal(hammock_root: Path) -> None:
    with pytest.raises(MCPToolError, match="invalid task_id"):
        await update_task(
            job_slug=JOB,
            stage_id=STAGE,
            task_id="../../etc/passwd",
            status="DONE",
            root=hammock_root,
        )


async def test_open_ask_rejects_bad_task_id(hammock_root: Path) -> None:
    with pytest.raises(MCPToolError, match="invalid task_id"):
        await open_ask(
            job_slug=JOB,
            stage_id=STAGE,
            kind="ask",
            text="q",
            task_id="../escape",
            root=hammock_root,
            poll_interval=0.02,
            timeout=0.05,
        )


async def test_append_stages_rejects_bad_stage_id(hammock_root: Path) -> None:
    with pytest.raises(MCPToolError, match=r"invalid stage id"):
        await append_stages(
            job_slug=JOB,
            stages=[{"id": "../escape", "worker": "agent"}],
            root=hammock_root,
        )
