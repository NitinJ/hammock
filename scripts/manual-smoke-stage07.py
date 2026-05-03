"""Stage 07 manual smoke — HIL plane: state machine, contract, orphan sweeper.

Usage::

    uv run python scripts/manual-smoke-stage07.py

Exercises:
1. State machine — valid and invalid transitions.
2. HilContract.get_open_items — default + filtered queries.
3. HilContract.submit_answer — first submit, idempotent re-submit, conflict.
4. OrphanSweeper — sweeps awaiting items for a stage, leaves others alone.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from dashboard.hil.contract import ConflictError, HilContract, HilFilter
from dashboard.hil.orphan_sweeper import OrphanSweeper
from dashboard.hil.state_machine import InvalidTransitionError, transition
from dashboard.state.cache import Cache
from shared.atomic import atomic_write_json
from shared.models.hil import AskAnswer, AskQuestion, HilItem, ReviewAnswer, ReviewQuestion
from shared.paths import hil_item_path

TS_STR = "2026-05-01T12:00:00+00:00"


def _ask(item_id: str, *, stage_id: str = "s1", status: str = "awaiting") -> HilItem:
    from datetime import UTC, datetime

    ts = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    return HilItem(
        id=item_id,
        kind="ask",
        stage_id=stage_id,
        created_at=ts,
        status=status,  # type: ignore[arg-type]
        question=AskQuestion(text="Should I use Argon2id?"),
        answer=AskAnswer(text="yes", choice=None) if status == "answered" else None,
        answered_at=ts if status == "answered" else None,
    )


def _review(item_id: str, *, stage_id: str = "s1") -> HilItem:
    from datetime import UTC, datetime

    return HilItem(
        id=item_id,
        kind="review",
        stage_id=stage_id,
        created_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        status="awaiting",
        question=ReviewQuestion(target="spec.md", prompt="Approve this spec?"),
    )


# ---------------------------------------------------------------------------
# 1. State machine
# ---------------------------------------------------------------------------


def smoke_state_machine() -> None:
    print("\n── 1. State machine ──────────────────────")
    item = _ask("h1")

    updated = transition(item, "answered")
    assert updated.status == "answered"
    assert item.status == "awaiting"
    print("  ✓ awaiting → answered OK (original unchanged)")

    updated2 = transition(_ask("h2"), "cancelled")
    assert updated2.status == "cancelled"
    print("  ✓ awaiting → cancelled OK")

    try:
        transition(updated, "cancelled")
        raise AssertionError("should have raised")
    except InvalidTransitionError:
        print("  ✓ answered → cancelled raises InvalidTransitionError")

    try:
        transition(item, "awaiting")
        raise AssertionError("should have raised")
    except InvalidTransitionError:
        print("  ✓ awaiting → awaiting (self) raises InvalidTransitionError")


# ---------------------------------------------------------------------------
# 2 & 3. HilContract
# ---------------------------------------------------------------------------


async def smoke_contract(root: Path) -> None:
    print("\n── 2. HilContract.get_open_items ─────────")
    items = [
        _ask("ask-1", stage_id="spec"),
        _ask("ask-2", stage_id="spec", status="answered"),
        _review("rev-1", stage_id="spec"),
        _ask("ask-3", stage_id="implement"),
    ]
    for item in items:
        atomic_write_json(hil_item_path("smoke-job", item.id, root=root), item)

    cache = await Cache.bootstrap(root)
    contract = HilContract(cache=cache, root=root)

    open_all = contract.get_open_items()
    assert len(open_all) == 3, f"expected 3 awaiting, got {len(open_all)}"
    print(f"  ✓ default filter → {len(open_all)} awaiting items")

    by_kind = contract.get_open_items(HilFilter(kind="review"))
    assert len(by_kind) == 1 and by_kind[0].id == "rev-1"
    print(f"  ✓ kind=review → 1 item ({by_kind[0].id})")

    by_stage = contract.get_open_items(HilFilter(stage_id="implement"))
    assert len(by_stage) == 1 and by_stage[0].id == "ask-3"
    print(f"  ✓ stage_id=implement → 1 item ({by_stage[0].id})")

    answered_items = contract.get_open_items(HilFilter(status="answered"))
    assert len(answered_items) == 1 and answered_items[0].id == "ask-2"
    print(f"  ✓ status=answered → 1 item ({answered_items[0].id})")

    print("\n── 3. HilContract.submit_answer ──────────")
    answer = AskAnswer(text="yes, use Argon2id", choice=None)
    updated = contract.submit_answer("ask-1", answer)
    assert updated.status == "answered"
    assert updated.answer == answer
    assert updated.answered_at is not None
    print("  ✓ submit_answer transitions awaiting → answered")

    # Idempotent re-submit
    second = contract.submit_answer("ask-1", answer)
    assert second.status == "answered"
    print("  ✓ identical re-submit is a no-op")

    # Conflict
    try:
        contract.submit_answer("ask-1", AskAnswer(text="different", choice=None))
        raise AssertionError("should have raised ConflictError")
    except ConflictError:
        print("  ✓ different answer on answered item raises ConflictError")

    # Cache is updated
    cached = cache.get_hil("ask-1")
    assert cached is not None and cached.status == "answered"
    print("  ✓ cache updated synchronously after submit_answer")


# ---------------------------------------------------------------------------
# 4. OrphanSweeper
# ---------------------------------------------------------------------------


def smoke_sweeper(root: Path) -> None:
    print("\n── 4. OrphanSweeper ──────────────────────")
    sweep_root = root / "sweep-root"
    items = [
        _ask("sw-1", stage_id="crashed-stage"),
        _ask("sw-2", stage_id="crashed-stage"),
        _ask("sw-3", stage_id="other-stage"),
        _ask("sw-4", stage_id="crashed-stage", status="answered"),
    ]
    for item in items:
        atomic_write_json(hil_item_path("sweep-job", item.id, root=sweep_root), item)

    sweeper = OrphanSweeper(root=sweep_root)
    cancelled = sweeper.sweep("sweep-job", "crashed-stage")
    assert set(cancelled) == {"sw-1", "sw-2"}, f"unexpected cancelled: {cancelled}"
    print(f"  ✓ sweep cancelled {cancelled} (awaiting items only)")

    # Idempotent
    second = sweeper.sweep("sweep-job", "crashed-stage")
    assert second == [], f"expected [] on second sweep, got {second}"
    print("  ✓ second sweep is a no-op (already cancelled)")

    # other-stage untouched
    from shared.models.hil import HilItem as _HilItem

    path = hil_item_path("sweep-job", "sw-3", root=sweep_root)
    sw3 = _HilItem.model_validate_json(path.read_text())
    assert sw3.status == "awaiting"
    print("  ✓ items in other stages untouched")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        smoke_state_machine()
        await smoke_contract(root)
        smoke_sweeper(root)
    print("\n✓ Stage 7 smoke complete — HIL plane functional end-to-end.")


if __name__ == "__main__":
    asyncio.run(main())
