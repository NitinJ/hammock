"""Integration-test fixtures: live dashboard + FakeEngine.

Stage 1, Step 0 stub. Fixture bodies raise NotImplementedError; Step 2
implements them. Step 1 tests import these fixtures and will fail with
the NotImplementedError when run, which is the expected Step-1 signal.

Design — see docs/hammock-impl-patch.md §1.5.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from tests.integration.fake_engine import FakeEngine


@dataclass
class DashboardHandle:
    """Handle to a live, running dashboard for one test.

    Attributes:
      client:  Async HTTP client for REST + SSE assertions. Uses
               ASGITransport so SSE streams are not buffered (Starlette's
               TestClient buffers full responses; httpx + ASGITransport
               does not).
      url:     ``http://127.0.0.1:<port>`` where uvicorn is bound. Used
               by Playwright in Stage 6; pytest tests prefer ``client``.
      root:    The hammock root the dashboard is configured against.
               Same path is passed to ``FakeEngine``.
    """

    client: httpx.AsyncClient
    url: str
    root: Path


@pytest.fixture
async def dashboard(tmp_path: Path) -> AsyncIterator[DashboardHandle]:
    """Boot a live dashboard against ``tmp_path`` with watcher running.

    Lifespan: bootstraps cache, starts watcher / supervisor / MCP-manager
    background tasks (run_background_tasks=True). Cleanup cancels those
    tasks and shuts down uvicorn cleanly.

    Tests that need to wait for disk-state propagation should poll the
    dashboard's API or SSE stream — never sleep for fixed durations.
    """
    raise NotImplementedError
    # Mypy needs the yield even though it's unreachable.
    yield  # type: ignore[unreachable]


@pytest.fixture
async def fake_engine(dashboard: DashboardHandle) -> FakeEngine:
    """A ``FakeEngine`` bound to the live dashboard's root with a fresh
    job slug. The slug is unique per test invocation."""
    raise NotImplementedError
