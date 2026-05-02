"""Route tests for ``/api/jobs``."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestListJobs:
    def test_returns_200(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/jobs")
        assert r.status_code == 200

    def test_returns_all_three_jobs(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/jobs")
        slugs = {item["job_slug"] for item in r.json()}
        assert slugs == {"alpha-job-1", "alpha-job-2", "beta-job-1"}

    def test_filter_by_project(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/jobs?project=alpha")
        slugs = {item["job_slug"] for item in r.json()}
        assert slugs == {"alpha-job-1", "alpha-job-2"}

    def test_filter_by_status(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/jobs?status=COMPLETED")
        slugs = {item["job_slug"] for item in r.json()}
        assert slugs == {"alpha-job-2"}

    def test_invalid_status_returns_422(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/jobs?status=NOT_A_STATE")
        assert r.status_code == 422


class TestGetJob:
    def test_happy_path(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/jobs/alpha-job-1")
        assert r.status_code == 200
        body = r.json()
        assert body["job"]["job_slug"] == "alpha-job-1"
        assert body["total_cost_usd"] == 2.25
        # Three stages, ordered by started_at
        assert [s["stage_id"] for s in body["stages"]] == ["design", "implement", "review"]

    def test_404_unknown(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/jobs/nope")
        assert r.status_code == 404
