"""Route tests for ``/api/artifacts``."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestArtifacts:
    def test_serves_markdown(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/artifacts/alpha-job-1/design-spec.md")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/markdown")
        assert "# design spec" in r.text

    def test_404_unknown_job(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/artifacts/nope/foo.md")
        assert r.status_code == 404

    def test_404_missing_file(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/artifacts/alpha-job-1/missing.md")
        assert r.status_code == 404

    def test_400_path_traversal(self, client: TestClient) -> None:
        with client:
            r = client.get("/api/artifacts/alpha-job-1/../../etc/passwd")
        # path traversal escapes job dir → 400 (or 404 if normalised away)
        assert r.status_code in {400, 404}
