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
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from rich.logging import RichHandler

from dashboard.api import router
from dashboard.settings import Settings
from dashboard.state.cache import Cache
from dashboard.state.pubsub import InProcessPubSub
from dashboard.watcher import tailer
from dashboard.watcher.tailer import CacheChange


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)],
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings  # type: ignore[attr-defined]

    cache = await Cache.bootstrap(settings.root)
    pubsub: InProcessPubSub[CacheChange] = InProcessPubSub()

    app.state.cache = cache  # type: ignore[attr-defined]
    app.state.pubsub = pubsub  # type: ignore[attr-defined]

    tasks = [
        asyncio.create_task(tailer.run(cache, pubsub), name="watcher"),
    ]
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
    return app
