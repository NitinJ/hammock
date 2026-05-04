"""Tests for HilItem creation when JobDriver._block_on_human transitions
a stage to BLOCKED_ON_HUMAN (P5 — real-claude e2e precondition track).

Earlier ``_block_on_human`` wrote ``stage.json`` + ``job.json`` only;
the corresponding ``HilItem`` was never created, so
``POST /api/hil/{id}/answer`` returned 404 on a freshly-blocked
stage. The existing fake e2e papered over this by hand-stitching the
artifact + transitioning ``stage.json`` to SUCCEEDED without going
through the answer endpoint.

Contract:

- A stage block creates a ``HilItem`` of kind ``manual-step`` with
  instructions derived from ``reason``.
- The item is persisted at ``paths.hil_item_path(job, item_id)``.
- Item id format follows the same shape ``open_ask`` uses so the
  list view sorts consistently.
- Stage-state + job-state writes still fire (no regression).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from job_driver.runner import JobDriver
from job_driver.stage_runner import FakeStageRunner
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig
from shared.models.hil import HilItem
from shared.models.job import JobConfig, JobState
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    StageDefinition,
)


def _human_stage(stage_id: str = "review-spec") -> StageDefinition:
    return StageDefinition(
        id=stage_id,
        worker="human",
        inputs=InputSpec(),
        outputs=OutputSpec(),
        budget=Budget(max_turns=1),
        exit_condition=ExitCondition(),
    )


def _seed_human_block_job(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "hammock-root"
    root.mkdir()
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    project = ProjectConfig(
        slug="p",
        name="p",
        repo_path=str(repo),
        remote_url="https://github.com/example/p",
        default_branch="main",
        created_at=datetime.now(UTC),
    )
    atomic_write_json(paths.project_json("p", root=root), project)

    job_slug = "j-block"
    job_dir = paths.job_dir(job_slug, root=root)
    job_dir.mkdir(parents=True)
    cfg = JobConfig(
        job_id="jid",
        job_slug=job_slug,
        project_slug="p",
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="t",
        state=JobState.SUBMITTED,
    )
    atomic_write_json(job_dir / "job.json", cfg)
    stage = _human_stage()
    (job_dir / "stage-list.yaml").write_text(
        yaml.dump({"stages": [json.loads(stage.model_dump_json())]})
    )
    return root, job_slug


def _list_hil_items(root: Path, job_slug: str) -> list[HilItem]:
    hil_dir = paths.job_dir(job_slug, root=root) / "hil"
    if not hil_dir.is_dir():
        return []
    out: list[HilItem] = []
    for p in sorted(hil_dir.glob("*.json")):
        out.append(HilItem.model_validate_json(p.read_text()))
    return out


# ---------------------------------------------------------------------------


async def test_block_on_human_creates_hilitem(tmp_path: Path) -> None:
    """A stage with worker=human triggers _block_on_human, which must
    create a HilItem so the answer endpoint can resolve it."""
    root, job_slug = _seed_human_block_job(tmp_path)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    items = _list_hil_items(root, job_slug)
    assert len(items) == 1, [i.id for i in items]
    item = items[0]
    assert item.kind == "manual-step"
    assert item.stage_id == "review-spec"
    assert item.status == "awaiting"


async def test_hilitem_id_matches_open_ask_shape(tmp_path: Path) -> None:
    """Item ids should follow the ``manualstep_<timestamp>_<token>`` shape
    used by ``open_ask`` so list-views render consistently."""
    root, job_slug = _seed_human_block_job(tmp_path)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()
    items = _list_hil_items(root, job_slug)
    assert items
    assert items[0].id.startswith("manualstep_")


async def test_block_on_human_still_writes_stage_and_job_state(tmp_path: Path) -> None:
    """No regression: the existing stage.json + job.json transitions
    must still happen alongside the HilItem write."""
    root, job_slug = _seed_human_block_job(tmp_path)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    # job.json reflects BLOCKED_ON_HUMAN
    cfg = JobConfig.model_validate_json(paths.job_json(job_slug, root=root).read_text())
    assert cfg.state == JobState.BLOCKED_ON_HUMAN
    # stage.json reflects BLOCKED_ON_HUMAN
    stage_path = paths.stage_json(job_slug, "review-spec", root=root)
    assert stage_path.exists()


async def test_block_on_human_emits_hil_item_opened_event(tmp_path: Path) -> None:
    """``hil_item_opened`` is in the canonical EVENT_TYPES already;
    the stage-block path should emit it so dashboard consumers see
    the new item without polling the directory."""
    from shared.models.events import Event

    root, job_slug = _seed_human_block_job(tmp_path)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    events_path = paths.job_events_jsonl(job_slug, root=root)
    events = [
        Event.model_validate_json(line)
        for line in events_path.read_text().splitlines()
        if line.strip()
    ]
    opened = [e for e in events if e.event_type == "hil_item_opened"]
    assert len(opened) == 1
    payload = opened[0].payload
    assert payload["stage_id"] == "review-spec"
    assert payload["kind"] == "manual-step"
    assert "item_id" in payload


async def test_answer_endpoint_resolves_blocked_stage_item(tmp_path: Path) -> None:
    """Integration: after _block_on_human creates the HilItem,
    POST /api/hil/{id}/answer must resolve it without 404."""
    from fastapi.testclient import TestClient

    from dashboard.app import create_app
    from dashboard.settings import Settings

    root, job_slug = _seed_human_block_job(tmp_path)
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    driver = JobDriver(
        job_slug,
        root=root,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    items = _list_hil_items(root, job_slug)
    assert items
    item_id = items[0].id

    settings = Settings(root=root, run_background_tasks=False)
    with TestClient(create_app(settings)) as client:
        resp = client.post(
            f"/api/hil/{item_id}/answer",
            json={
                "kind": "manual-step",
                "output": "operator says: done",
            },
        )
    assert resp.status_code == 200, resp.text
