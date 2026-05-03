"""Route tests for ``/api/hil``."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestListHil:
    def test_default_status_awaiting(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/hil")
        assert r.status_code == 200
        ids = {item["item_id"] for item in r.json()}
        assert ids == {"hil-open-ask", "hil-open-review"}

    def test_filter_by_kind(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/hil?kind=ask")
        ids = {item["item_id"] for item in r.json()}
        assert ids == {"hil-open-ask"}

    def test_filter_by_project(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/hil?project=alpha")
        ids = {item["item_id"] for item in r.json()}
        assert ids == {"hil-open-ask"}

    def test_oldest_first(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/hil")
        ids = [item["item_id"] for item in r.json()]
        assert ids == ["hil-open-ask", "hil-open-review"]


class TestGetHilItem:
    def test_happy_path(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/hil/hil-open-ask")
        assert r.status_code == 200
        body = r.json()
        # Stage 13: GET /api/hil/{id} returns HilItemDetail envelope
        assert body["item"]["id"] == "hil-open-ask"
        assert body["item"]["kind"] == "ask"
        assert body["job_slug"] == "alpha-job-1"
        assert body["ui_template_name"] == "ask-default-form"

    def test_404_unknown(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/hil/nope")
        assert r.status_code == 404
