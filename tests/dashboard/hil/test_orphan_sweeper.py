"""Tests for OrphanSweeper — cancel awaiting HIL items on stage restart."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from dashboard.hil.orphan_sweeper import OrphanSweeper
from shared.atomic import atomic_write_json
from shared.models.hil import AskAnswer, AskQuestion, HilItem
from shared.paths import hil_item_path

TS = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _ask_item(item_id: str, *, stage_id: str = "s1", status: str = "awaiting") -> HilItem:
    return HilItem(
        id=item_id,
        kind="ask",
        stage_id=stage_id,
        created_at=TS,
        status=status,  # type: ignore[arg-type]
        question=AskQuestion(text="question?"),
        answer=AskAnswer(text="yes", choice=None) if status == "answered" else None,
        answered_at=TS if status == "answered" else None,
    )


def _write(item: HilItem, job_slug: str, root: Path) -> None:
    atomic_write_json(hil_item_path(job_slug, item.id, root=root), item)


def _read(item_id: str, job_slug: str, root: Path) -> HilItem:
    path = hil_item_path(job_slug, item_id, root=root)
    return HilItem.model_validate_json(path.read_text())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sweep_cancels_awaiting_items(tmp_path: Path) -> None:
    _write(_ask_item("h1", stage_id="s1"), "job-a", tmp_path)
    _write(_ask_item("h2", stage_id="s1"), "job-a", tmp_path)

    sweeper = OrphanSweeper(root=tmp_path)
    cancelled = sweeper.sweep("job-a", "s1")

    assert set(cancelled) == {"h1", "h2"}
    assert _read("h1", "job-a", tmp_path).status == "cancelled"
    assert _read("h2", "job-a", tmp_path).status == "cancelled"


def test_sweep_ignores_answered_items(tmp_path: Path) -> None:
    _write(_ask_item("h1", status="answered"), "job-a", tmp_path)

    sweeper = OrphanSweeper(root=tmp_path)
    cancelled = sweeper.sweep("job-a", "s1")

    assert cancelled == []
    assert _read("h1", "job-a", tmp_path).status == "answered"


def test_sweep_ignores_already_cancelled_items(tmp_path: Path) -> None:
    _write(_ask_item("h1", status="cancelled"), "job-a", tmp_path)

    sweeper = OrphanSweeper(root=tmp_path)
    cancelled = sweeper.sweep("job-a", "s1")

    assert cancelled == []


def test_sweep_only_targets_matching_stage(tmp_path: Path) -> None:
    _write(_ask_item("h1", stage_id="s1"), "job-a", tmp_path)
    _write(_ask_item("h2", stage_id="s2"), "job-a", tmp_path)

    sweeper = OrphanSweeper(root=tmp_path)
    cancelled = sweeper.sweep("job-a", "s1")

    assert cancelled == ["h1"]
    assert _read("h1", "job-a", tmp_path).status == "cancelled"
    assert _read("h2", "job-a", tmp_path).status == "awaiting"


def test_sweep_empty_when_no_items(tmp_path: Path) -> None:
    sweeper = OrphanSweeper(root=tmp_path)
    assert sweeper.sweep("job-a", "s1") == []


def test_sweep_is_idempotent(tmp_path: Path) -> None:
    _write(_ask_item("h1", stage_id="s1"), "job-a", tmp_path)

    sweeper = OrphanSweeper(root=tmp_path)
    first = sweeper.sweep("job-a", "s1")
    second = sweeper.sweep("job-a", "s1")

    assert first == ["h1"]
    assert second == []  # already cancelled on first sweep
    assert _read("h1", "job-a", tmp_path).status == "cancelled"


def test_sweep_does_not_touch_other_jobs(tmp_path: Path) -> None:
    _write(_ask_item("h1", stage_id="s1"), "job-a", tmp_path)
    _write(_ask_item("h2", stage_id="s1"), "job-b", tmp_path)

    sweeper = OrphanSweeper(root=tmp_path)
    cancelled = sweeper.sweep("job-a", "s1")

    assert cancelled == ["h1"]
    assert _read("h2", "job-b", tmp_path).status == "awaiting"
