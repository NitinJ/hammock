"""FastAPI application factory + lifespan.

The lifespan context manager (per design doc § Presentation plane
§ Process structure):

1. Bootstraps the cache from the hammock root.
2. Creates an in-process pub/sub bus.
3. Starts the filesystem watcher as a background asyncio task.
4. On shutdown, cancels all background tasks and awaits them.

Stage 8 starts only the watcher task.  Driver supervisor and MCP manager
land in Stages 4/6 and will be wired here then.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from dashboard.api import router
from dashboard.driver.supervisor import Supervisor
from dashboard.mcp.manager import MCPManager
from dashboard.settings import Settings
from dashboard.state.cache import Cache
from dashboard.state.pubsub import InProcessPubSub
from dashboard.watcher import tailer
from dashboard.watcher.tailer import CacheChange
from shared.models import Event

_FRONTEND_DIST: Path = Path(__file__).parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings: Settings = app.state.settings  # type: ignore[attr-defined]

    cache = await Cache.bootstrap(settings.root)
    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()
    # Stage 12.5 (A5): separate bus for typed Event records tailed from events.jsonl
    events_pubsub: InProcessPubSub[Event] = InProcessPubSub()

    supervisor = Supervisor()
    mcp_manager = MCPManager()

    app.state.cache = cache  # type: ignore[attr-defined]
    app.state.pubsub = pubsub  # type: ignore[attr-defined]
    app.state.events_pubsub = events_pubsub  # type: ignore[attr-defined]
    app.state.supervisor = supervisor  # type: ignore[attr-defined]
    app.state.mcp_manager = mcp_manager  # type: ignore[attr-defined]

    # v0 alignment Plan #7: presentation-plane spec calls for the
    # lifespan to start watcher + supervisor + MCP manager. Earlier
    # drafts shipped only the watcher (Codex review of the audit
    # caught the gap; both other classes also lacked `run()` methods,
    # which this PR adds). ``run_background_tasks`` is the test
    # opt-out — TestClient suites that pre-seed jobs would race the
    # supervisor's first scan (which fires on startup, would spawn
    # drivers, and would conflict with API calls under test).
    tasks: list[asyncio.Task[Any]] = []
    if settings.run_background_tasks:
        tasks.extend(
            [
                asyncio.create_task(tailer.run(cache, pubsub, events_pubsub), name="watcher"),
                asyncio.create_task(supervisor.run(root=settings.root), name="supervisor"),
                asyncio.create_task(mcp_manager.run(), name="mcp-manager"),
            ]
        )
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the FastAPI application.

    *settings* is injected here (rather than read inside ``lifespan``) so
    tests can supply a custom ``hammock_root`` without environment mutation.
    """
    if settings is None:
        settings = Settings()
    app = FastAPI(title="Hammock Dashboard", lifespan=lifespan)
    app.state.settings = settings  # type: ignore[attr-defined]
    app.include_router(router)
    _mount_spa(app)
    return app


def _mount_spa(app: FastAPI) -> None:
    """Mount the Vue SPA at `/` if the bundle exists.

    The frontend build lands in `dashboard/frontend/dist/`; we mount its
    `assets/` directory for hashed JS/CSS and add a catch-all fallback
    for the client-side router so deep links like `/jobs/<slug>` return
    `index.html` instead of 404. If the bundle isn't built yet, we mount
    nothing and only the JSON API is served (the operator gets a 404 on
    `/` but the JSON API still works — the runbook tells them to run
    the frontend build).
    """
    if not (_FRONTEND_DIST / "index.html").exists():
        return

    assets = _FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="spa-assets")

    index_html = _FRONTEND_DIST / "index.html"

    @app.get("/{spa_path:path}", include_in_schema=False)
    async def _spa_fallback(  # pyright: ignore[reportUnusedFunction]
        spa_path: str,
    ) -> FileResponse:
        # API + SSE routes are matched by their own routers because they
        # were registered first; this catch-all only fires for paths the
        # API doesn't claim. Reject anything that looks like an API miss
        # so the SPA isn't served in place of a real 404.
        if spa_path.startswith(("api/", "sse/")):
            raise HTTPException(status_code=404)
        return FileResponse(index_html)
