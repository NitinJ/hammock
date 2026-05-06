"""Tests for the FastAPI application shell (Stage 8).

Covers:
- GET /api/health → 200, correct payload
- Cache size reflects actual cache contents after bootstrap
- Lifespan startup + shutdown clean (no leaked tasks, no warnings)
- app.state populated by lifespan (cache, pubsub accessible)
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.api import HealthResponse
from dashboard.app import create_app
from dashboard.settings import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_app(root: Path) -> TestClient:
    """Create a TestClient backed by a fresh app pointing at *root*."""
    settings = Settings(root=root)
    app = create_app(settings)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_returns_200(self, tmp_path: Path) -> None:
        with make_app(tmp_path) as client:
            response = client.get("/api/health")
        assert response.status_code == 200

    def test_payload_shape(self, tmp_path: Path) -> None:
        with make_app(tmp_path) as client:
            response = client.get("/api/health")
        data = response.json()
        parsed = HealthResponse.model_validate(data)
        assert parsed.ok is True
        # Stage 3: cache_size removed (no cache). Runner mode + binary
        # remain — operator confirmation that this dashboard would burn
        # real spend if it spawned a job.
        assert parsed.runner_mode == "real"
        assert parsed.claude_binary == "claude"

    def test_runner_mode_fake_when_fixtures_set(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from dashboard.app import create_app
        from dashboard.settings import Settings

        fixtures = tmp_path / "fakes"
        fixtures.mkdir()
        settings = Settings(root=tmp_path, fake_fixtures_dir=fixtures)
        with TestClient(create_app(settings)) as client:
            data = client.get("/api/health").json()
        assert data["runner_mode"] == "fake"
        # claude_binary is suppressed in fake mode (irrelevant + avoids
        # implying we'd shell out to it).
        assert data["claude_binary"] is None

    def test_health_ok_is_true(self, tmp_path: Path) -> None:
        with make_app(tmp_path) as client:
            response = client.get("/api/health")
        assert response.json()["ok"] is True


# ---------------------------------------------------------------------------
# SPA serving — `/` returns the Vue bundle, deep links fall back to
# index.html so client-side routing works, /api/ + /sse/ misses still 404.
# ---------------------------------------------------------------------------


class TestSpaMount:
    def test_spa_root_serves_index_html(self, tmp_path: Path) -> None:
        from dashboard.app import _FRONTEND_DIST

        with make_app(tmp_path) as client:
            response = client.get("/")
        # If the bundle exists in the dev tree, `/` should return the SPA;
        # if it doesn't (CI without pnpm build), `/` 404s and only the API
        # is served. Either is documented behaviour. The assertion below
        # only fires if the bundle exists, which keeps the test useful
        # for the dev workflow without forcing CI to pre-build the SPA.
        if (_FRONTEND_DIST / "index.html").exists():
            assert response.status_code == 200
            assert "<!doctype html>" in response.text.lower()

    def test_spa_deep_link_serves_index_html(self, tmp_path: Path) -> None:
        from dashboard.app import _FRONTEND_DIST

        with make_app(tmp_path) as client:
            response = client.get("/jobs/some-slug/stages/some-stage")
        if (_FRONTEND_DIST / "index.html").exists():
            assert response.status_code == 200
            assert "<!doctype html>" in response.text.lower()

    def test_unknown_api_path_still_404s(self, tmp_path: Path) -> None:
        """The SPA catch-all must not swallow misses on /api/ — operators
        rely on real 404s for API typos."""
        with make_app(tmp_path) as client:
            response = client.get("/api/no-such-endpoint")
        assert response.status_code == 404

    def test_unknown_sse_path_still_404s(self, tmp_path: Path) -> None:
        with make_app(tmp_path) as client:
            response = client.get("/sse/no-such-stream")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Lifespan — app.state population
# ---------------------------------------------------------------------------


class TestLifespan:
    def test_no_cache_on_app_state(self, tmp_path: Path) -> None:
        """Stage 3: no in-memory cache. app.state.cache must not exist."""
        settings = Settings(root=tmp_path)
        app = create_app(settings)
        with TestClient(app):
            assert not hasattr(app.state, "cache")

    def test_pubsub_on_app_state(self, tmp_path: Path) -> None:
        from dashboard.state.pubsub import InProcessPubSub

        settings = Settings(root=tmp_path)
        app = create_app(settings)
        with TestClient(app):
            assert isinstance(app.state.pubsub, InProcessPubSub)

    def test_lifespan_clean_shutdown_no_exception(self, tmp_path: Path) -> None:
        """Entering and exiting the lifespan raises no exception."""
        settings = Settings(root=tmp_path)
        app = create_app(settings)
        # If lifespan errors, TestClient raises on __enter__ or __exit__
        with TestClient(app):
            pass  # just startup + shutdown

    def test_multiple_requests_within_lifespan(self, tmp_path: Path) -> None:
        with make_app(tmp_path) as client:
            r1 = client.get("/api/health")
            r2 = client.get("/api/health")
        assert r1.status_code == 200
        assert r2.status_code == 200


# ---------------------------------------------------------------------------
# Settings — env-driven configuration
# ---------------------------------------------------------------------------


class TestSettings:
    def test_default_host(self) -> None:
        s = Settings(root=Path("/tmp"))
        assert s.host == "127.0.0.1"

    def test_default_port(self) -> None:
        s = Settings(root=Path("/tmp"))
        assert s.port == 8765

    def test_env_override_root(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAMMOCK_ROOT", str(tmp_path))
        s = Settings()
        assert s.root == tmp_path

    def test_env_override_port(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAMMOCK_PORT", "9999")
        monkeypatch.setenv("HAMMOCK_ROOT", "/tmp")
        s = Settings()
        assert s.port == 9999

    def test_env_override_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HAMMOCK_HOST", "0.0.0.0")
        monkeypatch.setenv("HAMMOCK_ROOT", "/tmp")
        s = Settings()
        assert s.host == "0.0.0.0"
