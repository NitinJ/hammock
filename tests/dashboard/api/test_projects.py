"""Route tests for ``/api/projects``."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestListProjects:
    def test_returns_200(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/projects")
        assert r.status_code == 200

    def test_returns_both_projects(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/projects")
        slugs = {item["slug"] for item in r.json()}
        assert slugs == {"alpha", "beta"}

    def test_carries_doctor_status(self, client: TestClient) -> None:
        with client:
            data = client.get("/api/projects").json()
        by_slug = {p["slug"]: p for p in data}
        assert by_slug["alpha"]["doctor_status"] == "pass"
        assert by_slug["beta"]["doctor_status"] == "warn"


class TestGetProject:
    def test_happy_path(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/projects/alpha")
        assert r.status_code == 200
        body = r.json()
        assert body["project"]["slug"] == "alpha"
        assert body["total_jobs"] == 2

    def test_404_unknown(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/projects/nope")
        assert r.status_code == 404
