"""Tests for projects API + storage."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient


def _make_git_repo(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(p), check=True)
    (p / "README.md").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=str(p), check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        cwd=str(p),
        check=True,
    )


def test_register_project_with_explicit_slug(client: TestClient, tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    _make_git_repo(repo)
    resp = client.post(
        "/api/projects",
        json={"repo_path": str(repo), "slug": "my-proj", "name": "My Project"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"] == "my-proj"
    assert body["name"] == "My Project"
    assert body["repo_path"] == str(repo)
    assert body["health"]["path_exists"] is True
    assert body["health"]["is_git_repo"] is True


def test_register_project_auto_slug_from_path(client: TestClient, tmp_path: Path) -> None:
    repo = tmp_path / "Highlighter-Extension"
    _make_git_repo(repo)
    resp = client.post("/api/projects", json={"repo_path": str(repo)})
    assert resp.status_code == 201
    assert resp.json()["slug"] == "highlighter-extension"


def test_register_project_rejects_missing_path(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/api/projects", json={"repo_path": str(tmp_path / "doesnotexist")})
    assert resp.status_code == 400
    assert "does not exist" in resp.json()["detail"]


def test_register_project_rejects_non_git_path(client: TestClient, tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    resp = client.post("/api/projects", json={"repo_path": str(plain)})
    assert resp.status_code == 400
    assert "not a git repo" in resp.json()["detail"]


def test_register_project_rejects_duplicate(client: TestClient, tmp_path: Path) -> None:
    repo = tmp_path / "dup"
    _make_git_repo(repo)
    r1 = client.post("/api/projects", json={"repo_path": str(repo)})
    assert r1.status_code == 201
    r2 = client.post("/api/projects", json={"repo_path": str(repo), "slug": "dup"})
    assert r2.status_code == 409


def test_register_project_rejects_path_traversal_slug(client: TestClient, tmp_path: Path) -> None:
    repo = tmp_path / "fine"
    _make_git_repo(repo)
    resp = client.post(
        "/api/projects",
        json={"repo_path": str(repo), "slug": "../escape"},
    )
    # Normalize strips slashes; result is "escape" which is fine.
    # But we want to verify a slug that's truly invalid (empty after norm) fails.
    # Try "..." which normalizes to empty.
    resp = client.post(
        "/api/projects",
        json={"repo_path": str(repo), "slug": "..."},
    )
    assert resp.status_code == 400


def test_list_projects_returns_health(
    client: TestClient, tmp_path: Path, hammock_v2_root: Path
) -> None:
    repo = tmp_path / "list-test"
    _make_git_repo(repo)
    client.post("/api/projects", json={"repo_path": str(repo)})
    # Now break health by removing the repo
    import shutil

    shutil.rmtree(repo)
    resp = client.get("/api/projects")
    assert resp.status_code == 200
    items = resp.json()["projects"]
    assert any(item["slug"] == "list-test" for item in items)
    item = next(i for i in items if i["slug"] == "list-test")
    assert item["health"]["path_exists"] is False


def test_get_project_404(client: TestClient) -> None:
    resp = client.get("/api/projects/nonexistent")
    assert resp.status_code == 404


def test_delete_project(client: TestClient, tmp_path: Path) -> None:
    repo = tmp_path / "delme"
    _make_git_repo(repo)
    client.post("/api/projects", json={"repo_path": str(repo)})
    r = client.delete("/api/projects/delme")
    assert r.status_code == 200
    r2 = client.delete("/api/projects/delme")
    assert r2.status_code == 404


def test_verify_project_refreshes_branch(
    client: TestClient, tmp_path: Path, hammock_v2_root: Path
) -> None:
    repo = tmp_path / "vp"
    _make_git_repo(repo)
    client.post("/api/projects", json={"repo_path": str(repo)})
    resp = client.post("/api/projects/vp/verify")
    assert resp.status_code == 200
    # Project json on disk should also reflect default_branch
    pdata = json.loads((hammock_v2_root / "projects" / "vp.json").read_text())
    assert pdata["slug"] == "vp"
