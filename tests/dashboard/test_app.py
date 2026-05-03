"""Tests for the FastAPI application shell (Stage 8).

Covers:
- GET /api/health → 200, correct payload
- Cache size reflects actual cache contents after bootstrap
- Lifespan startup + shutdown clean (no leaked tasks, no warnings)
- app.state populated by lifespan (cache, pubsub accessible)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.api import HealthResponse
from dashboard.app import create_app
from dashboard.settings import Settings
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig

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
        # Validates against Pydantic model
        parsed = HealthResponse.model_validate(data)
        assert parsed.ok is True
        assert isinstance(parsed.cache_size, int)
        # Stage 16 follow-up — runner mode + binary surfaced for operator
        # confirmation. Defaults: real mode (no fixtures set), claude="claude".
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

    def test_empty_root_cache_size_zero(self, tmp_path: Path) -> None:
        with make_app(tmp_path) as client:
            response = client.get("/api/health")
        assert response.json()["cache_size"] == 0

    def test_cache_size_reflects_bootstrap(self, tmp_path: Path) -> None:
        """cache_size is the sum of all entity counts populated at startup."""
        # Write one project so bootstrap sees it
        project_dir = tmp_path / "projects" / "alpha"
        project_dir.mkdir(parents=True)
        project = ProjectConfig(
            slug="alpha",
            name="Alpha",
            repo_path="/tmp/alpha",
            default_branch="main",
            created_at=datetime(2026, 1, 1, tzinfo=datetime.now().astimezone().tzinfo),
        )
        atomic_write_json(project_dir / "project.json", project)

        with make_app(tmp_path) as client:
            response = client.get("/api/health")
        assert response.json()["cache_size"] == 1

    def test_health_ok_is_true(self, tmp_path: Path) -> None:
        with make_app(tmp_path) as client:
            response = client.get("/api/health")
        assert response.json()["ok"] is True


# ---------------------------------------------------------------------------
# Lifespan — app.state population
# ---------------------------------------------------------------------------


class TestLifespan:
    def test_cache_on_app_state(self, tmp_path: Path) -> None:
        from dashboard.state.cache import Cache

        settings = Settings(root=tmp_path)
        app = create_app(settings)
        with TestClient(app):
            assert isinstance(app.state.cache, Cache)

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
