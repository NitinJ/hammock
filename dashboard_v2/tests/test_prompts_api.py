"""Tests for the aggregate prompts API."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi.testclient import TestClient


def _make_git_repo(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(p), check=True)
    (p / "README.md").write_text("hi")
    subprocess.run(["git", "add", "-A"], cwd=str(p), check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@x",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        cwd=str(p),
        check=True,
    )


def test_list_bundled_only(client: TestClient) -> None:
    resp = client.get("/api/prompts/bundled")
    assert resp.status_code == 200
    data = resp.json()
    names = [p["name"] for p in data["prompts"]]
    # Bundled set includes the orchestrator + canonical node prompts
    assert "orchestrator" in names
    for p in data["prompts"]:
        assert p["source"] == "bundled"
        assert p["size"] >= 0
        assert p["modified_at"]


def test_get_bundled_prompt_content(client: TestClient) -> None:
    resp = client.get("/api/prompts/bundled/orchestrator")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "orchestrator"
    assert body["source"] == "bundled"
    assert body["content"]


def test_get_bundled_prompt_404(client: TestClient) -> None:
    assert client.get("/api/prompts/bundled/nope").status_code == 404


def test_aggregate_includes_project_prompts(client: TestClient, tmp_path: Path) -> None:
    repo = tmp_path / "proj-with-prompts"
    _make_git_repo(repo)
    # Register project
    resp = client.post("/api/projects", json={"repo_path": str(repo), "slug": "proj-prompts"})
    assert resp.status_code == 201, resp.text
    # Add a prompt under the project
    resp = client.post(
        "/api/projects/proj-prompts/prompts",
        json={"name": "custom-review", "content": "# Custom\n\nReview."},
    )
    assert resp.status_code == 201, resp.text

    # Aggregate list contains both bundled + the new project prompt
    resp = client.get("/api/prompts")
    assert resp.status_code == 200
    items = resp.json()["prompts"]
    sources = {item["source"] for item in items}
    assert "bundled" in sources
    assert "proj-prompts" in sources
    custom = [p for p in items if p["source"] == "proj-prompts"]
    assert any(p["name"] == "custom-review" for p in custom)


def test_filter_by_source(client: TestClient, tmp_path: Path) -> None:
    repo = tmp_path / "filter-proj"
    _make_git_repo(repo)
    client.post("/api/projects", json={"repo_path": str(repo), "slug": "filter-proj"})
    client.post(
        "/api/projects/filter-proj/prompts",
        json={"name": "p1", "content": "x"},
    )
    # Filter to that project
    resp = client.get("/api/prompts?source=filter-proj")
    items = resp.json()["prompts"]
    assert items
    assert all(item["source"] == "filter-proj" for item in items)
    # Filter to bundled
    resp = client.get("/api/prompts?source=bundled")
    items = resp.json()["prompts"]
    assert all(item["source"] == "bundled" for item in items)


def test_filter_unknown_source_404(client: TestClient) -> None:
    resp = client.get("/api/prompts?source=does-not-exist")
    assert resp.status_code == 404
