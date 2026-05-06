"""FastAPI application factory + lifespan.

Per impl-patch §Stage 3: the lifespan no longer bootstraps an in-memory
cache. The watcher tails the hammock root; subscribers (mainly SSE
handlers) read disk on demand to materialize responses.

Lifespan steps:

1. Create the in-process pub/sub buses (PathChange + typed Event).
2. Start the filesystem watcher (tails root, classifies via
   ``dashboard.state.classify``, publishes to pubsub).
3. Start the driver supervisor + MCP manager.
4. On shutdown, cancel all background tasks and await them.
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
from dashboard.state.pubsub import InProcessPubSub
from dashboard.watcher import tailer
from dashboard.watcher.tailer import PathChange
from shared.models import Event

_FRONTEND_DIST: Path = Path(__file__).parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings: Settings = app.state.settings  # type: ignore[attr-defined]

    pubsub: InProcessPubSub[PathChange] = InProcessPubSub()
    events_pubsub: InProcessPubSub[Event] = InProcessPubSub()

    supervisor = Supervisor()
    mcp_manager = MCPManager()

    app.state.pubsub = pubsub  # type: ignore[attr-defined]
    app.state.events_pubsub = events_pubsub  # type: ignore[attr-defined]
    app.state.supervisor = supervisor  # type: ignore[attr-defined]
    app.state.mcp_manager = mcp_manager  # type: ignore[attr-defined]

    # Ensure the watch root exists before spawning the watcher; awatch
    # fails on missing directories.
    settings.root.mkdir(parents=True, exist_ok=True)

    tasks: list[asyncio.Task[Any]] = []
    if settings.run_background_tasks:
        tasks.extend(
            [
                asyncio.create_task(
                    tailer.run(settings.root, pubsub, events_pubsub),
                    name="watcher",
                ),
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
