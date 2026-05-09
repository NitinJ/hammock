"""Test fixtures for dashboard v2."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def hammock_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Set HAMMOCK_ROOT to a tmpdir for the duration of the test."""
    root = tmp_path / "hammock-v2-root"
    root.mkdir()
    (root / "jobs").mkdir()
    monkeypatch.setenv("HAMMOCK_ROOT", str(root))
    monkeypatch.setenv("HAMMOCK_RUNNER_MODE", "fake")
    yield root


@pytest.fixture
def client(hammock_root: Path) -> Iterator[TestClient]:
    """FastAPI test client. Forces fresh app construction per test so
    settings are re-evaluated."""
    # Re-import to pick up env vars
    from dashboard.api.app import create_app

    app = create_app()
    with TestClient(app) as c:
        yield c
