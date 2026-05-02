"""Route tests for the observatory metrics stub."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestObservatoryMetrics:
    def test_returns_200_with_zero_payload(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/observatory/metrics")
        assert r.status_code == 200
        body = r.json()
        assert body["sampled_events"] == 0
        assert body["proposals_emitted"] == 0
        assert body["reviewer_verdicts"] == 0
