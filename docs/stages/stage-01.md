# Stage 1 — Storage layer + cache + watchfiles

**PR:** [#2](https://github.com/NitinJ/hammock/pull/2) (merged 2026-05-02)
**Branch:** `feat/stage-01-storage-cache`
**Commit on `main`:** `3bd16b5` (squash-merged as `5f9cc8a`)

## What was built

The read-side state layer. Disk is canonical; the cache is an in-memory denormalisation, kept in sync via `watchfiles`. Pub/sub bridges filesystem changes to in-process subscribers.

- **`dashboard/state/cache.py`** — typed in-memory cache for the four state-file kinds (`project.json`, `job.json`, `stage.json`, `hil/<id>.json`). Bootstrap walks the root once; subsequent updates flow through `apply_change`. Stream files (`events.jsonl`, `messages.jsonl`, `tool-uses.jsonl`, `nudges.jsonl`) are NOT cached — Stage 10's SSE layer reads them on demand.
- **`dashboard/state/pubsub.py`** — `InProcessPubSub[T]`, generic, scope-keyed, asyncio-native. One `asyncio.Queue` per subscription. Slow subscribers don't block fast ones; opt into bounded queues via `subscribe(maxsize=N)`. `WeakSet` holds subscriptions so dangling subscribers don't pin memory.
- **`dashboard/watcher/tailer.py`** — `watchfiles.awatch` loop with tunable debounce (default 100ms, down from watchfiles' 1600ms). Maps each change to scopes (`global`, `project:<slug>`, `job:<slug>`, `stage:<job>:<sid>`) and publishes a `CacheChange` notification.
- **`dashboard/settings.py`** — env-driven runtime settings (just `hammock_root` for now; expanded by Stage 8).
- **50 new tests** under `tests/dashboard/{state,watcher}/` — bootstrap, apply_change, classify_path, scope filtering, pubsub semantics (subscribe/publish, scope isolation, slow-subscriber drop, ordering, aclose unregisters), tailer integration with a fake watchfiles stream.
- **`scripts/manual-smoke-stage1.py`** — end-to-end against a real filesystem and real `watchfiles.awatch`. Modifies + deletes files; asserts each subscriber sees the corresponding change.
- **CI extended** — pyright now strict on `shared/` + `dashboard/`; pytest covers all of `tests/`; backend workflow `paths` filter watches `dashboard/`, `job_driver/`, `cli/` too.

## Notable design decisions made during implementation

1. **PubSub is generic over message type** (`InProcessPubSub[T]`) rather than fixed to `shared.models.Event`. Stage 1 publishes `CacheChange` notifications; Stage 10 will use the same bus for typed `Event`s tailed from `events.jsonl`. Single envelope would force coercion.
2. **Cache scope is narrow.** Only state files. Append-only logs are read-on-demand (cost rollups, archival, SSE replay) — caching them would burn memory for no value.
3. **`global` scope always fires alongside specific scopes.** Simplifies cross-job views (home page, HIL queue) without per-job subscriptions.
4. **Default debounce 100ms, not 1600ms.** `watchfiles`' default would blow the design's "<100ms propagation" SLA on every state-file change.
5. **`asyncio.QueueFull` → silent drop** for slow subscribers in v0. Production setup would surface drops as events; v0 trade-off is "never block fast subscribers."
6. **HIL items attach to `job:<slug>` scope, not their own.** Per-HIL-item scopes would balloon channel count without giving any consumer a useful subscription. Job + global cover the access patterns.
7. **PEP 695 generic syntax** (`class PubSubSubscription[T]`) instead of `Generic[T]`. Modern style; matches our 3.12+ floor.
8. **`Subscriber.deliver()` is public** (was `_queue.put_nowait` directly). Lets pyright enforce the `_queue` private boundary while still allowing the bus to push.

## Locked for downstream stages

- **Cache shape is stable.** `bootstrap`, `get_*`, `list_*`, `apply_change`, `size`. Stage 9 will add projections that read from the cache; the cache itself doesn't grow methods.
- **Scope keys are canonical.** `"global"`, `"project:<slug>"`, `"job:<slug>"`, `"stage:<job>:<sid>"`. Stage 10's SSE handlers MUST use the same keys.
- **`InProcessPubSub` is the only sanctioned pub/sub.** No Redis, no broker. v0 is single-process, single-machine.
- **`Cache.bootstrap` is async.** v0 implementation is sync I/O behind it; future async I/O won't break the call site.
- **Stream files (`events.jsonl` & friends) are NOT cached.** Stage 10's SSE replay reads them directly from disk. The cache only holds typed state.

## Files added/modified (22)

```
dashboard/__init__.py
dashboard/settings.py
dashboard/state/__init__.py
dashboard/state/cache.py
dashboard/state/pubsub.py
dashboard/watcher/__init__.py
dashboard/watcher/tailer.py

tests/dashboard/__init__.py
tests/dashboard/state/__init__.py
tests/dashboard/state/test_cache.py
tests/dashboard/state/test_pubsub.py
tests/dashboard/watcher/__init__.py
tests/dashboard/watcher/test_tailer.py

scripts/manual-smoke-stage1.py
docs/stages/README.md           (new — index for stage summaries)
docs/stages/stage-00.md         (new — Stage 0 summary, started this convention)

pyproject.toml                  (+watchfiles dep, +dashboard package, +pyright dashboard/)
uv.lock
.github/workflows/backend.yml   (paths filter expanded; pyright covers dashboard/)
```

## Dependencies introduced

| Layer | Package | Version | Purpose |
|---|---|---|---|
| runtime | `watchfiles` | `1.1.1` | Filesystem watch — Rust-backed, async-native |
| transitive | `anyio` | `4.13.0` | Pulled by `watchfiles` |
| transitive | `idna` | `3.13` | Pulled by `anyio` |

## Acceptance criteria — met

- [x] Bootstrap of 100 jobs <500ms (verified in `test_bootstrap_100_jobs_under_500ms`; runs in <10ms locally)
- [x] File appearance reflected in cache within ~100ms (default debounce)
- [x] Invalid JSON logs warning and is skipped; cache stays at last-good state
- [x] PubSub: per-scope isolation, no cross-pollination
- [x] Memory: one Pydantic instance per file (dict-keyed, no copies)
- [x] CI green on matrix py3.12 + py3.13

## Notes for downstream stages

- **Stage 8 (FastAPI shell)** wires the cache + watcher + pubsub into the FastAPI lifespan. The lifespan code in `presentation-plane.md` is the template; just import and wire. Don't subclass; compose.
- **Stage 9 (HTTP API + projections)** reads from `Cache.list_*` and `Cache.get_*`. Projections are pure functions; pass the cache snapshot or instance.
- **Stage 10 (SSE delivery + replay)**:
  - Subscribe to scopes via `pubsub.subscribe(scope)`. Use `aclose()` on disconnect.
  - Replay from disk reads the on-disk `events.jsonl` directly (cache doesn't hold them). Filter by `Last-Event-ID`.
  - The SSE handler can use `InProcessPubSub[Event]` instead of `[CacheChange]` if it switches to publishing typed `Event` objects from a separate event-tailer task. Or run two pubsub instances. Don't try to multiplex one bus across both message types.
- **Stage 4 (Job Driver)** writes the state files this stage reads. Use `shared.atomic.atomic_write_json` so the cache never sees a partial write.
- **WSL2 quirk**: `watchfiles` cold-start latency is ~500-800ms. The smoke script sleeps 1s before first mutation. Real production startup includes app boot time so this is invisible; tests should account for it via fake stream injection (which our existing tests do).
- **`Cache.list_hil(status=…)`** is the building block for the HIL queue view (Stage 12) and the awaiting-count metric on home (Stage 12).
