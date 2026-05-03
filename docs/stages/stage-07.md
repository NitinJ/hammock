# Stage 7 — HIL plane realisation

**PR:** TBD (in PR)
**Branch:** `feat/stage-07-hil-plane`

## What was built

The HIL domain layer: a pure state machine, a contract for querying and
answering HIL items, and an orphan sweeper that cancels stranded items on
stage restart. After this stage, the HIL plane is complete — the remaining
missing piece is the HTTP/form transport (Stage 13).

- **`dashboard/hil/state_machine.py`** — `transition(item, new_status) →
  HilItem`. Pure function (no I/O). Validates `awaiting → answered` and
  `awaiting → cancelled`. Terminal states (`answered`, `cancelled`) raise
  `InvalidTransitionError` on any further transition. Returns a new
  `model_copy` — original is never mutated.

- **`dashboard/hil/contract.py`** — `HilContract(cache, root)` exposes:
  - `get_open_items(filter: HilFilter | None) → list[HilItem]` — reads
    from the in-memory cache. Default filter returns all `awaiting` items.
    `HilFilter` is a plain dataclass with optional `status`, `kind`,
    `job_slug`, and `stage_id` fields.
  - `submit_answer(item_id, answer) → HilItem` — validates the transition
    (raises `InvalidTransitionError` on cancelled items), detects
    idempotent re-submission (same answer → no-op), raises `ConflictError`
    on conflicting re-submission, writes the updated item atomically to
    disk, and calls `cache.apply_change` for immediate in-process
    consistency.

- **`dashboard/hil/orphan_sweeper.py`** — `OrphanSweeper(root).sweep(job_slug,
  stage_id) → list[str]`. Reads files directly from
  `hil/<id>.json` (no cache dependency — can run before the watcher has
  re-synced). Cancels every `awaiting` item whose `stage_id` matches;
  returns the list of cancelled ids. Idempotent.

## Design decisions

### No cache dependency in OrphanSweeper

The sweeper operates directly on disk rather than through the cache. On
stage restart the cache may not yet reflect the crashed state, and the
sweeper is called early in the restart path. Disk reads are authoritative;
the watcher picks up the written cancellations on its next scan.

### HilContract caches root from `cache.root`

If `root=None` is passed, `HilContract` falls back to `cache.root`
(the public property) rather than `cache._root`. This avoids accessing
protected attributes and keeps the contract usable when the caller only
has a cache reference.

### submit_answer idempotency window

Idempotency is checked by comparing the `answer` field on the already-
answered item against the incoming answer using Pydantic model equality.
The check is a full structural equality (`==`), so field order doesn't
matter. A second submit with an identical answer returns the existing
item unchanged, without writing to disk.

### Orphan sweeper is caller's responsibility

The spec says the sweeper is "hooked into Job Driver's stage-restart path
(callback wiring in Stage 4's supervisor)." Stage 7 delivers the sweeper
as a standalone class. The wiring into the job driver is left for a later
stage; Stage 7 does not reach into `job_driver/` to avoid creating a
cross-module dependency before Stage 4 stabilises.

## Files added/modified

```
dashboard/hil/__init__.py          (new)
dashboard/hil/state_machine.py     (new)
dashboard/hil/contract.py          (new)
dashboard/hil/orphan_sweeper.py    (new)
tests/dashboard/hil/__init__.py    (new)
tests/dashboard/hil/test_state_machine.py  (new — 9 tests)
tests/dashboard/hil/test_contract.py       (new — 13 tests)
tests/dashboard/hil/test_orphan_sweeper.py (new — 7 tests)
scripts/manual-smoke-stage07.py    (new)
```

## Dependencies introduced

None. All types imported from `shared.models.hil` (Stage 0) and
`dashboard.state.cache` (Stage 1). No new packages.

## Acceptance criteria status

| Criterion | Status |
|---|---|
| State machine transitions match design doc § HIL lifecycle | ✓ |
| `get_open_items()` returns all `awaiting` items with optional filter | ✓ |
| `submit_answer()` is idempotent — same answer is no-op | ✓ |
| `submit_answer()` with different answer is rejected (ConflictError) | ✓ |
| Orphan sweeper cancels all `awaiting` items for a stage | ✓ |
| No imports from `dashboard/api/` (Domain/Transport split) | ✓ |
| ruff + pyright clean on new files | ✓ |
| 504 tests pass (28 new) | ✓ |

## Notes for downstream stages

- **Stage 13 (HTTP form pipeline):** `HilContract.submit_answer` is the
  canonical write path. The HTTP handler should instantiate `HilContract`
  with `app.state.cache` and call `submit_answer`. No direct disk writes
  from the route layer.

- **Orphan sweep wiring:** The sweep call site is not yet wired. When
  the Job Driver's restart path stabilises, `OrphanSweeper(root=root).sweep(job_slug, stage_id)`
  should be called once per stage-restart before spawning the new agent
  session.

- **HilFilter.status default is `"awaiting"`:** `get_open_items()` with
  no argument is the "open queue" view. Pass `HilFilter(status=None)` to
  get all items regardless of status.
