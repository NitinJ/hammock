"""Tests for HilContract — get_open_items and submit_answer."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dashboard.hil.contract import ConflictError, HilContract, HilFilter, NotFoundError
from dashboard.hil.state_machine import InvalidTransitionError
from dashboard.state.cache import Cache
from shared.atomic import atomic_write_json
from shared.models.hil import (
    AskAnswer,
    AskQuestion,
    HilItem,
    ReviewAnswer,
    ReviewQuestion,
)
from shared.paths import hil_item_path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _review_item(item_id: str, *, stage_id: str = "s1", status: str = "awaiting") -> HilItem:
    return HilItem(
        id=item_id,
        kind="review",
        stage_id=stage_id,
        created_at=TS,
        status=status,  # type: ignore[arg-type]
        question=ReviewQuestion(target="spec.md", prompt="approve?"),
        answer=ReviewAnswer(decision="approve", comments="lgtm") if status == "answered" else None,
        answered_at=TS if status == "answered" else None,
    )


def _setup(
    tmp_path: Path,
    *,
    job_slug: str,
    items: list[HilItem],
) -> tuple[Cache, Path]:
    """Write items to disk and bootstrap a cache. Returns (cache, root)."""
    for item in items:
        path = hil_item_path(job_slug, item.id, root=tmp_path)
        atomic_write_json(path, item)
    cache = asyncio.run(Cache.bootstrap(tmp_path))
    return cache, tmp_path


# ---------------------------------------------------------------------------
# get_open_items
# ---------------------------------------------------------------------------


def test_get_open_items_default_returns_awaiting(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[
            _ask_item("h1", status="awaiting"),
            _ask_item("h2", status="answered"),
            _ask_item("h3", status="cancelled"),
        ],
    )
    contract = HilContract(cache=cache, root=root)
    items = contract.get_open_items()
    assert len(items) == 1
    assert items[0].id == "h1"


def test_get_open_items_filter_by_kind(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[
            _ask_item("h1"),
            _review_item("h2"),
        ],
    )
    contract = HilContract(cache=cache, root=root)
    items = contract.get_open_items(HilFilter(kind="review"))
    assert len(items) == 1
    assert items[0].id == "h2"


def test_get_open_items_filter_by_stage_id(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[
            _ask_item("h1", stage_id="stage-a"),
            _ask_item("h2", stage_id="stage-b"),
        ],
    )
    contract = HilContract(cache=cache, root=root)
    items = contract.get_open_items(HilFilter(stage_id="stage-a"))
    assert len(items) == 1
    assert items[0].id == "h1"


def test_get_open_items_filter_by_job_slug(tmp_path: Path) -> None:
    cache, _root = _setup(
        tmp_path,
        job_slug="job-a",
        items=[_ask_item("h1")],
    )
    # Write a second job's item
    h2 = _ask_item("h2")
    atomic_write_json(hil_item_path("job-b", "h2", root=tmp_path), h2)
    cache = asyncio.run(Cache.bootstrap(tmp_path))

    contract = HilContract(cache=cache, root=tmp_path)
    items = contract.get_open_items(HilFilter(job_slug="job-a"))
    assert len(items) == 1
    assert items[0].id == "h1"


def test_get_open_items_filter_by_status_answered(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[
            _ask_item("h1", status="awaiting"),
            _ask_item("h2", status="answered"),
        ],
    )
    contract = HilContract(cache=cache, root=root)
    items = contract.get_open_items(HilFilter(status="answered"))
    assert len(items) == 1
    assert items[0].id == "h2"


def test_get_open_items_empty_when_none(tmp_path: Path) -> None:
    cache = asyncio.run(Cache.bootstrap(tmp_path))
    contract = HilContract(cache=cache, root=tmp_path)
    assert contract.get_open_items() == []


# ---------------------------------------------------------------------------
# submit_answer
# ---------------------------------------------------------------------------


def test_submit_answer_transitions_to_answered(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[_ask_item("h1", status="awaiting")],
    )
    contract = HilContract(cache=cache, root=root)
    answer = AskAnswer(text="use it", choice=None)
    updated = contract.submit_answer("h1", answer)
    assert updated.status == "answered"
    assert updated.answer == answer


def test_submit_answer_sets_answered_at(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[_ask_item("h1", status="awaiting")],
    )
    contract = HilContract(cache=cache, root=root)
    updated = contract.submit_answer("h1", AskAnswer(text="yes", choice=None))
    assert updated.answered_at is not None


def test_submit_answer_persists_to_disk(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[_ask_item("h1", status="awaiting")],
    )
    contract = HilContract(cache=cache, root=root)
    contract.submit_answer("h1", AskAnswer(text="yes", choice=None))

    from shared.paths import hil_dir

    disk_path = next(hil_dir("proj-job", root=root).glob("*.json"))
    from_disk = HilItem.model_validate_json(disk_path.read_text())
    assert from_disk.status == "answered"
    assert from_disk.answer is not None


def test_submit_answer_updates_cache(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[_ask_item("h1", status="awaiting")],
    )
    contract = HilContract(cache=cache, root=root)
    contract.submit_answer("h1", AskAnswer(text="yes", choice=None))
    cached = cache.get_hil("h1")
    assert cached is not None
    assert cached.status == "answered"


def test_submit_answer_idempotent_same_answer(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[_ask_item("h1", status="awaiting")],
    )
    contract = HilContract(cache=cache, root=root)
    answer = AskAnswer(text="yes", choice=None)
    first = contract.submit_answer("h1", answer)
    second = contract.submit_answer("h1", answer)  # same answer — should not raise
    assert second.status == "answered"
    assert second.answer == first.answer


def test_submit_answer_conflict_different_answer(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[_ask_item("h1", status="awaiting")],
    )
    contract = HilContract(cache=cache, root=root)
    contract.submit_answer("h1", AskAnswer(text="yes", choice=None))
    with pytest.raises(ConflictError):
        contract.submit_answer("h1", AskAnswer(text="no", choice=None))


def test_submit_answer_not_found_raises(tmp_path: Path) -> None:
    cache = asyncio.run(Cache.bootstrap(tmp_path))
    contract = HilContract(cache=cache, root=tmp_path)
    with pytest.raises(NotFoundError):
        contract.submit_answer("nonexistent", AskAnswer(text="yes", choice=None))


def test_submit_answer_cancelled_item_raises(tmp_path: Path) -> None:
    cache, root = _setup(
        tmp_path,
        job_slug="proj-job",
        items=[_ask_item("h1", status="cancelled")],
    )
    contract = HilContract(cache=cache, root=root)
    with pytest.raises(InvalidTransitionError):
        contract.submit_answer("h1", AskAnswer(text="yes", choice=None))
