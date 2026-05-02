"""Route tests for ``/api/costs``."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestCostRollup:
    def test_job_scope(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/costs?scope=job&id=alpha-job-1")
        assert r.status_code == 200
        body = r.json()
        assert body["total_usd"] == 2.25
        assert body["by_stage"]["design"] == 1.25
        assert body["by_stage"]["implement"] == 1.0

    def test_project_scope(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/costs?scope=project&id=alpha")
        assert r.status_code == 200
        assert r.json()["total_usd"] == 2.25

    def test_stage_scope_requires_job_param(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/costs?scope=stage&id=design")
        assert r.status_code == 422

    def test_stage_scope_with_job(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/costs?scope=stage&id=design&job=alpha-job-1")
        assert r.status_code == 200
        assert r.json()["total_usd"] == 1.25

    def test_404_unknown_project(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/costs?scope=project&id=nope")
        assert r.status_code == 404

    def test_invalid_scope_returns_422(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/costs?scope=galaxy&id=x")
        assert r.status_code == 422

    def test_missing_id_returns_422(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/costs?scope=job")
        assert r.status_code == 422
