"""Integration-test fixtures: live dashboard + FakeEngine.

Stage 1 deliverable. The ``dashboard`` fixture boots the real FastAPI
app (``dashboard.app.create_app``) with the watcher / supervisor / MCP
manager background tasks running, exposes an in-process httpx async
client (via ASGITransport, so SSE streams aren't buffered) AND launches
a uvicorn server on a free localhost port for browser-driven access
(used by Playwright in Stage 6).

Design — see docs/hammock-impl-patch.md §1.5.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import uvicorn

from dashboard.app import create_app
from dashboard.settings import Settings
from tests.integration.fake_engine import FakeEngine

log = logging.getLogger(__name__)


@dataclass
class DashboardHandle:
    """Handle to a live, running dashboard for one test.

    Attributes:
      client:  Async HTTP client for REST + SSE assertions. Uses
               ASGITransport so SSE streams are not buffered.
      url:     ``http://127.0.0.1:<port>`` where uvicorn is bound.
               Used by Playwright in Stage 6; pytest tests prefer
               ``client`` (faster, no network hop).
      root:    The hammock root the dashboard is configured against.
               Same path is passed to ``FakeEngine``.
    """

    client: httpx.AsyncClient
    url: str
    root: Path


def _free_localhost_port() -> int:
    """Allocate a free TCP port on 127.0.0.1 for uvicorn to bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for_server(url: str, *, timeout: float = 5.0) -> None:
    """Poll the health endpoint until uvicorn is accepting connections."""
    deadline = asyncio.get_event_loop().time() + timeout
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=1.0) as raw:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await raw.get(f"{url}/api/health")
                if resp.status_code == 200:
                    return
            except (httpx.ConnectError, httpx.ReadError) as exc:
                last_err = exc
            await asyncio.sleep(0.05)
    raise RuntimeError(f"dashboard did not become ready at {url}: {last_err!r}")


@pytest.fixture
async def dashboard(tmp_path: Path) -> AsyncIterator[DashboardHandle]:
    """Boot a live dashboard against ``tmp_path`` with the watcher
    running. Cleanup cancels lifespan tasks and shuts uvicorn down."""
    settings = Settings(
        root=tmp_path,
        run_background_tasks=True,
    )
    app = create_app(settings)

    port = _free_localhost_port()
    url = f"http://127.0.0.1:{port}"

    # uvicorn for browser-reachable access (Stage 6 / Playwright).
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    server_task = asyncio.create_task(server.serve(), name="uvicorn-fixture")

    try:
        await _wait_for_server(url)
    except Exception:
        server.should_exit = True
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server_task, timeout=2.0)
        raise

    # In-process httpx client for fast pytest assertions (REST + SSE).
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url=url, timeout=5.0)

    handle = DashboardHandle(client=client, url=url, root=tmp_path)
    try:
        yield handle
    finally:
        await client.aclose()
        server.should_exit = True
        # uvicorn.serve runs the lifespan shutdown; await its completion.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(server_task, timeout=5.0)
        if not server_task.done():
            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await server_task


@pytest.fixture
async def fake_engine(dashboard: DashboardHandle) -> FakeEngine:
    """A ``FakeEngine`` bound to the live dashboard's root with a fresh
    job slug. The slug is unique per test invocation."""
    return FakeEngine(dashboard.root, "test-job-1")
