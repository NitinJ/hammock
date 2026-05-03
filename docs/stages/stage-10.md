# Stage 10 — SSE delivery + replay

**PR:** TBD (see branch `feat/stage-10-sse-delivery`)
**Branch:** `feat/stage-10-sse-delivery`
**Commit on `main`:** TBD (squash-merge target)

## What was built

The dashboard's real-time delivery surface. The frontend (Stage 11+) can now
open an `EventSource` to one of three scoped SSE channels and receive live
`CacheChange` events, with `Last-Event-ID` replay of on-disk events.jsonl
content on reconnect.

- **`dashboard/api/sse.py`** — Three SSE route handlers:
  - `GET /sse/global` — all scopes; subscribes to the global pubsub topic.
  - `GET /sse/job/{slug}` — job-scoped; subscribes to topic `job:{slug}`.
  - `GET /sse/stage/{slug}/{sid}` — stage-scoped; subscribes to topic
    `stage:{slug}:{sid}`.
  Each handler parses `Last-Event-ID`, calls `_event_stream`, and wraps the
  result in a `StreamingResponse` with `Content-Type: text/event-stream` and
  `Cache-Control: no-cache` / `X-Accel-Buffering: no` headers.

- **`dashboard/state/pubsub.py`** — gains `replay_since(scope, last_id, root)`,
  an async generator that reads the relevant on-disk `events.jsonl` (stage- or
  job-level depending on scope), filters to `seq > last_id`, and yields
  `shared.models.Event` objects. Called in Phase 1 of `_event_stream` when the
  client reconnects with a `Last-Event-ID` header.

- **`dashboard/api/__init__.py`** — `include_router(sse.router)` added to the
  router aggregation block, making the three SSE routes visible in the OpenAPI
  schema and registered with the app on startup.

- **`_event_stream` generator** — drives Phase 1 (replay) then Phase 2 (live).
  Phase 2 runs a 3-way `asyncio.wait` race on every iteration:
  1. `msg_task` — next item from the subscriber's `asyncio.Queue`.
  2. `ka_task` — `asyncio.sleep(KEEPALIVE_INTERVAL)` (15 s).
  3. `dc_task` — `_poll_disconnected` (polls `request.is_disconnected()` every
     50 ms).
  Whichever completes first wins; the other two tasks are cancelled. The
  generator exits cleanly within ~50 ms of client disconnect rather than
  waiting up to 15 s.

- **Wire format** — replay events include `id: <seq>` so the browser updates
  its `Last-Event-ID`; live `CacheChange` events omit `id:` because they are
  not persisted and cannot be replayed. The `event:` field names the change
  kind (e.g. `stage_changed`, `job_changed`).

- **38 new tests**:
  - `tests/dashboard/api/test_sse.py` — 21 tests driving `_event_stream`
    directly via fake `Request` objects and a mock pubsub; covers content-type
    header, replay delivery, no-replay without header, keepalive emission,
    disconnect exit, and all three route scopes.
  - `tests/dashboard/state/test_pubsub_replay.py` — 17 tests for
    `replay_since`: empty file, missing file, seq filtering, multi-scope,
    corrupt lines skipped, large seq values.

## Notable design decisions made during implementation

1. **TestClient and httpx ASGITransport both buffer the entire response body.**
   Starlette 1.0.0's `TestClient` and `httpx.AsyncClient(transport=ASGITransport)`
   both wait for the streaming response to finish before returning, making them
   unsuitable for testing an indefinitely streaming SSE endpoint. Tests
   therefore drive `_event_stream` directly with a fake `Request` (a thin
   dataclass implementing `is_disconnected`) and a mock `InProcessPubSub`,
   bypassing the HTTP layer entirely. This is the recommended pattern for SSE
   generators in Starlette — test the generator, not the transport.

2. **Disconnect detection uses a 50 ms poll, not a dedicated signal.**
   `request.is_disconnected()` is the Starlette-provided hook; there is no
   lower-level ASGI disconnect event that can be awaited without holding a
   reference to the receive channel. A 50 ms polling interval keeps the
   generator exit latency negligible in practice and is far below any
   reasonable proxy timeout.

3. **`replay_since` is a free function, not a method on `InProcessPubSub`.**
   Replay reads from disk; the pubsub class manages in-process queues.
   Mixing them would make the pubsub class I/O-dependent and harder to test
   in isolation. The generator calls both independently.

4. **Replay events carry `id:`; live events do not.**
   SSE spec: the browser only updates its internal `Last-Event-ID` value when
   it receives a line with `id:`. Replay events have stable, monotonically
   increasing seq numbers from disk, so they are safe to use as IDs. Live
   `CacheChange` events are not persisted, so emitting a synthetic ID would
   mislead the browser into replaying them on reconnect.

5. **`KEEPALIVE_INTERVAL` is 15 seconds.**
   Chosen to survive default 30 s proxy idle timeouts with one keepalive per
   window. The constant is module-level in `sse.py` and overridable in tests
   by patching.

6. **Scope strings are plain Python strings, not an enum.**
   `"global"`, `"job:{slug}"`, `"stage:{slug}:{sid}"` — the pubsub topic is
   already keyed by these strings in Stage 8. Adding an enum would require
   matching on the pubsub side too; keeping strings avoids the coupling.

## Locked for downstream stages

- **Wire format is stable.** Stage 11 (frontend scaffold) will open
  `EventSource` to these URLs; the `event:` / `data:` / `id:` structure must
  not change without a coordinated bump.
- **`replay_since(scope, last_id, root)`** is the canonical replay API. Future
  stages that add new event kinds to `events.jsonl` get free replay with no
  SSE-layer changes.
- **SSE routes are GET-only and unauthenticated** at v0. Stage 16+ (auth)
  will add a dependency that reads a bearer token; the route signatures here
  accept an additional `Depends(...)` without breaking callers.
- **`_event_stream` is the only place that touches pubsub in the HTTP layer.**
  Keep it that way; route handlers must stay one-liners.

## Files added/modified

```
dashboard/api/sse.py                          (new)
dashboard/state/pubsub.py                     (added replay_since)
dashboard/api/__init__.py                     (router registration)

tests/dashboard/api/test_sse.py               (new — 21 tests)
tests/dashboard/state/test_pubsub_replay.py   (new — 17 tests)

scripts/manual-smoke-stage10.py               (new)

docs/stages/stage-10.md                       (this file)
docs/stages/README.md                         (index row added)
```

## Dependencies introduced

None. SSE streaming uses `fastapi.responses.StreamingResponse` (already
present via Stage 8) and stdlib `asyncio`. No new packages required.

## Acceptance criteria — met

- [x] `GET /sse/global`, `GET /sse/job/{slug}`, `GET /sse/stage/{slug}/{sid}`
      exist and return `Content-Type: text/event-stream`.
- [x] `Last-Event-ID: N` triggers replay of all on-disk events with `seq > N`.
- [x] No `id:` lines are emitted when `Last-Event-ID` is absent.
- [x] Generator exits within ~50 ms of client disconnect (50 ms poll).
- [x] Keepalive comment (`: keepalive`) emitted every 15 s of inactivity.
- [x] 38 new tests pass (21 SSE route + 17 replay); prior suite unaffected.
- [x] ruff + ruff format clean.
- [x] pyright strict on `shared/` + `dashboard/` clean.

## Notes for downstream stages

- **Stage 11 (Frontend scaffold)**: open `EventSource("/sse/global")` from
  the Vue app. The `event:` field names match `classified.kind + "_changed"`
  (e.g. `stage_changed`, `job_changed`, `hil_changed`). Parse `data:` as JSON;
  fields are `scope`, `change_kind`, `file_kind`, and optional `job_slug`,
  `stage_id`, `project_slug`, `hil_id`. On reconnect, the browser sends
  `Last-Event-ID` automatically; the server replays missed events.
- **Stage 12 (Read views)**: SSE patches complement TanStack Query snapshots.
  The pattern is: initial fetch via `GET /api/...`, then invalidate the
  relevant query on receipt of a matching SSE event. Do not re-fetch on every
  keepalive — filter by `event:` type and `scope` / `job_slug` / `stage_id`.
- **Stage 15 (Stage live view)**: stage-scoped SSE (`/sse/stage/{slug}/{sid}`)
  is the channel for the live agent output panel. When Stage 15 adds
  agent-stream events to `events.jsonl`, replay here delivers them on page
  reload with no SSE-layer changes.
- **Auth (Stage 16+)**: add `Depends(require_auth)` to the three route
  handlers in `sse.py`. The `_event_stream` generator itself needs no changes.
