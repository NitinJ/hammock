# Stage 8 — FastAPI shell + cache wiring

**PR:** [#4](https://github.com/NitinJ/hammock/pull/4) (merged 2026-05-02)
**Branch:** `feat/stage-08-fastapi-shell`
**Commit on `main`:** squash-merged into main

## What was built

The dashboard process shell. `python -m hammock.dashboard` now starts a live
FastAPI + uvicorn server. The lifespan context manager boots the cache, pub/sub
bus, and filesystem watcher; `GET /api/health` returns `{"ok": true, "cache_size": N}`.

- **`dashboard/__main__.py`** — entry point. `uvicorn.run(app, workers=1)` with
  rich-formatted logging. Single-worker is locked by design (splitting workers
  would split the cache).
- **`dashboard/app.py`** — `create_app(settings)` factory + `lifespan`
  context manager. Lifespan bootstraps `Cache`, creates `InProcessPubSub`,
  starts `watcher.run` as a named asyncio task (`"watcher"`), cancels all tasks
  on shutdown, and awaits them with `return_exceptions=True`.
- **`dashboard/settings.py`** — migrated from hand-rolled `os.environ` +
  `dataclass` to `pydantic-settings` `BaseSettings` (env prefix `HAMMOCK_`).
  Adds `host` (`HAMMOCK_HOST`, default `127.0.0.1`) and `port` (`HAMMOCK_PORT`,
  default `8765`). Field renamed `hammock_root` → `root` to avoid the
  double-prefix `HAMMOCK_HAMMOCK_ROOT`.
- **`dashboard/api/__init__.py`** — router aggregation skeleton. Ships
  `GET /api/health` with `HealthResponse(ok: bool, cache_size: int)`. Stage 9
  adds the business routers here.
- **14 new tests** under `tests/dashboard/test_app.py` — health endpoint
  (200, payload shape, empty root = 0, bootstrap reflection), lifespan
  app.state (Cache + InProcessPubSub present, clean shutdown, multiple
  requests), settings (defaults + `HAMMOCK_ROOT`/`HOST`/`PORT` env overrides).
- **`scripts/manual-smoke-stage08.py`** — subprocess smoke: start server,
  hit `/api/health`, assert `ok=True` + `cache_size=0`, SIGTERM, assert
  shutdown < 3 s.

## Notable design decisions made during implementation

1. **Settings injection via `app.state`** rather than module-level globals or
   env re-reads inside lifespan. `create_app(settings)` stores settings in
   `app.state.settings` before the lifespan runs; lifespan reads it back.
   This keeps tests hermetic — no `monkeypatch.setenv` needed for the
   lifespan path itself.

2. **`_configure_logging` lives in `__main__.py` only**, not `app.py`. Pyright
   strict raises `reportUnusedFunction` if a function is defined in one module
   but only called from another. Moving it to the entry point module keeps
   `app.py` clean for import in tests without side-effects.

3. **`AsyncGenerator[None, None]` annotation**, not `AsyncIterator[None]`.
   Starlette / FastAPI deprecated annotating `@asynccontextmanager` returns as
   `AsyncIterator`; pyright fires `reportDeprecated`. The correct annotation is
   `AsyncGenerator[None, None]` imported from `collections.abc`.

4. **`cache_size = sum(cache.size().values())`**. `Cache.size()` returns
   `dict[str, int]` with keys `projects`, `jobs`, `stages`, `hil`. The health
   endpoint sums them for a single diagnostic integer. Downstream stages can
   break this out if needed (Stage 9 projections expose per-entity counts).

5. **Watcher-only lifespan for Stage 8**. The design doc lifespan shows four
   tasks (watcher, driver_supervisor, mcp_manager, telegram_bot). Stages
   4 and 6 own driver_supervisor and mcp_manager respectively; they will wire
   into lifespan when they land. Stage 8 starts only the watcher.

6. **`httpx` added to `[dependency-groups] dev`**, not `[dependencies]`.
   `starlette.testclient.TestClient` requires httpx at import time. It is a
   test-only dependency; the running server does not use it.

## Locked for downstream stages

- **`create_app(settings: Settings | None = None) -> FastAPI`** is the
  canonical factory. Tests always use `create_app(Settings(root=tmp_path))`.
  Don't bypass it.
- **`app.state.cache`** is a `Cache` instance after lifespan startup.
- **`app.state.pubsub`** is an `InProcessPubSub[CacheChange]` instance.
- **`app.state.settings`** is the `Settings` instance used at startup.
- **`GET /api/health`** stays at that path. Stage 9 adds routes under
  `/api/projects`, `/api/jobs/`, etc. — no conflict.
- **Single uvicorn worker**. Locked. Do not change `workers=1` in
  `__main__.py`. Multiple workers → multiple cache copies → split state.
- **`HAMMOCK_ROOT` env var** controls the root. Tests use `Settings(root=…)`
  directly; the smoke script sets `HAMMOCK_ROOT` in subprocess env.

## Files added/modified

```
dashboard/__main__.py           (new)
dashboard/app.py                (new)
dashboard/api/__init__.py       (new)
dashboard/settings.py           (modified — pydantic-settings migration + host/port)

tests/dashboard/test_app.py     (new)

scripts/manual-smoke-stage08.py (new)

pyproject.toml   (+fastapi, +uvicorn, +pydantic-settings, +rich, +httpx dev)
uv.lock
```

## Dependencies introduced

| Layer | Package | Version | Purpose |
|---|---|---|---|
| runtime | `fastapi` | `0.115.x` | Web framework + OpenAPI |
| runtime | `uvicorn` | `0.46.0` | ASGI server |
| runtime | `pydantic-settings` | `2.14.0` | Env-driven settings |
| runtime | `rich` | `15.0.0` | RichHandler for structured logging |
| transitive | `starlette` | `1.0.0` | ASGI toolkit (pulled by fastapi) |
| transitive | `python-dotenv` | `1.2.2` | Pulled by pydantic-settings |
| dev | `httpx` | `0.28.1` | TestClient dependency |
| transitive | `httpcore` | `1.0.9` | Pulled by httpx |

## Acceptance criteria — met

- [x] Server starts in <1 s on a fresh hammock-root (smoke: ready in <300 ms)
- [x] Cache, watcher, and pubsub all instantiated and accessible via `app.state`
- [x] Graceful shutdown cancels all background tasks (smoke: SIGTERM → exit in 0.11 s)
- [x] No tests rely on real network — all in-process via `TestClient`
- [x] CI green on py3.12 + py3.13

## Notes for downstream stages

- **Stage 9 (HTTP API read endpoints)**: add routers to `dashboard/api/`. The
  pattern is `router = APIRouter(); @router.get(...)` in a new module, then
  `app.include_router(router)` in `create_app`. Access `app.state.cache` via
  `request.app.state.cache` in handlers, or inject via `Depends`.
- **Stage 10 (SSE)**: subscribe to `app.state.pubsub` from SSE handlers.
  The pubsub instance lives for the app lifetime; subscribe/unsubscribe per
  HTTP connection.
- **Stage 4/6 (Job Driver / MCP manager)**: wire their background tasks into
  `lifespan` in `dashboard/app.py`. Follow the existing pattern — append to
  `tasks`, and they will be cancelled on shutdown automatically.
- **Rich logging**: Stage 8 sets up `RichHandler` only in `__main__.py` (CLI
  path). Tests do NOT configure logging — pytest captures it by default.
  Don't add `_configure_logging()` calls to `app.py` or test fixtures.
