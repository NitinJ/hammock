"""Projects management API tests — frozen during Step 3.

Per ``docs/projects-management.md``: register / verify / delete +
existing list / detail. Tests use the live dashboard fixture and a
real ``git init`` repo on tmp_path so the verify pipeline (running
``git remote get-url`` + ``git symbolic-ref``) hits the real CLI.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.integration.conftest import DashboardHandle


def _git(args: list[str], *, cwd: Path) -> None:
    """Run a git command in *cwd*; fail loudly on non-zero."""
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


def _init_repo(
    parent: Path, name: str, *, remote_url: str = "https://github.com/me/repo.git"
) -> Path:
    """Initialise a fresh git repo with the canonical ``main`` default
    branch, an ``origin`` remote, and a single commit so HEAD resolves."""
    repo = parent / name
    repo.mkdir()
    _git(["init", "-b", "main"], cwd=repo)
    _git(["remote", "add", "origin", remote_url], cwd=repo)
    _git(["config", "user.email", "test@example.com"], cwd=repo)
    _git(["config", "user.name", "test"], cwd=repo)
    (repo / "README.md").write_text("hello\n")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)
    # Pretend origin/HEAD is set to main (without an actual remote we
    # do this by creating the symbolic-ref locally so symbolic-ref works).
    _git(["update-ref", "refs/remotes/origin/HEAD", "refs/heads/main"], cwd=repo)
    _git(["symbolic-ref", "refs/remotes/origin/HEAD", "refs/heads/main"], cwd=repo)
    return repo


# ---------------------------------------------------------------------------
# POST /api/projects (register)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_writes_project_json(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Happy path: a valid local checkout registers, project.json on
    disk has all the verified fields, response carries verify=pass."""
    src = _init_repo(tmp_path_factory.mktemp("src"), "my-app")
    resp = await dashboard.client.post("/api/projects", json={"path": str(src)})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["verify"]["status"] == "pass"
    assert body["verify"]["remote_url"] == "https://github.com/me/repo.git"
    assert body["verify"]["default_branch"] == "main"

    project = body["project"]
    assert project["slug"] == "my-app"
    assert project["repo_path"] == str(src)
    assert project["remote_url"] == "https://github.com/me/repo.git"
    assert project["default_branch"] == "main"
    assert project["last_health_check_status"] == "pass"
    assert project["last_health_check_at"] is not None

    pj = dashboard.root / "projects" / "my-app" / "project.json"
    assert pj.is_file()
    data = json.loads(pj.read_text())
    assert data["slug"] == "my-app"
    assert data["repo_path"] == str(src)
    assert data["remote_url"] == "https://github.com/me/repo.git"
    assert data["default_branch"] == "main"


@pytest.mark.asyncio
async def test_register_with_explicit_slug_and_name(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    src = _init_repo(tmp_path_factory.mktemp("src"), "my-app")
    resp = await dashboard.client.post(
        "/api/projects",
        json={"path": str(src), "slug": "custom-slug", "name": "Custom Name"},
    )
    assert resp.status_code == 201, resp.text
    project = resp.json()["project"]
    assert project["slug"] == "custom-slug"
    assert project["name"] == "Custom Name"


@pytest.mark.asyncio
async def test_register_missing_path_returns_400(dashboard: DashboardHandle) -> None:
    resp = await dashboard.client.post("/api/projects", json={"path": "/does/not/exist"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert any(
        "does not exist" in str(d).lower() or "not found" in str(d).lower()
        for d in (detail if isinstance(detail, list) else [detail])
    )


@pytest.mark.asyncio
async def test_register_non_git_path_returns_400(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A directory that exists but has no .git/ should be rejected with
    a clear reason."""
    src = tmp_path_factory.mktemp("not-a-repo")
    (src / "README.md").write_text("not a repo\n")
    resp = await dashboard.client.post("/api/projects", json={"path": str(src)})
    assert resp.status_code == 400
    body = resp.json()
    detail = body["detail"]
    flat = json.dumps(detail)
    assert "git" in flat.lower()


@pytest.mark.asyncio
async def test_register_duplicate_slug_returns_409(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    src = _init_repo(tmp_path_factory.mktemp("src"), "my-app")
    r1 = await dashboard.client.post("/api/projects", json={"path": str(src)})
    assert r1.status_code == 201, r1.text

    r2 = await dashboard.client.post("/api/projects", json={"path": str(src)})
    assert r2.status_code == 409, r2.text


# ---------------------------------------------------------------------------
# DELETE /api/projects/{slug}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_project_dir(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    src = _init_repo(tmp_path_factory.mktemp("src"), "my-app")
    await dashboard.client.post("/api/projects", json={"path": str(src)})

    resp = await dashboard.client.delete("/api/projects/my-app")
    assert resp.status_code == 204

    project_dir = dashboard.root / "projects" / "my-app"
    assert not project_dir.exists()


@pytest.mark.asyncio
async def test_delete_unknown_slug_returns_404(dashboard: DashboardHandle) -> None:
    resp = await dashboard.client.delete("/api/projects/nope")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/verify
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reverify_updates_last_health_check(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    src = _init_repo(tmp_path_factory.mktemp("src"), "my-app")
    r1 = await dashboard.client.post("/api/projects", json={"path": str(src)})
    first_check = r1.json()["project"]["last_health_check_at"]

    # Re-run verify; timestamp should advance.
    import asyncio

    await asyncio.sleep(0.05)
    r2 = await dashboard.client.post("/api/projects/my-app/verify")
    assert r2.status_code == 200, r2.text
    second_check = r2.json()["project"]["last_health_check_at"]
    assert second_check > first_check, (first_check, second_check)
    assert r2.json()["verify"]["status"] == "pass"


@pytest.mark.asyncio
async def test_reverify_unknown_slug_returns_404(dashboard: DashboardHandle) -> None:
    resp = await dashboard.client.post("/api/projects/nope/verify")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET (already exists; spot-check the new fields surface)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_includes_health_fields(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    src = _init_repo(tmp_path_factory.mktemp("src"), "my-app")
    await dashboard.client.post("/api/projects", json={"path": str(src)})

    resp = await dashboard.client.get("/api/projects")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["slug"] == "my-app"
    assert item["remote_url"] == "https://github.com/me/repo.git"
    assert item["default_branch"] == "main"
    assert item["last_health_check_status"] == "pass"


@pytest.mark.asyncio
async def test_get_detail_includes_health_fields(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    src = _init_repo(tmp_path_factory.mktemp("src"), "my-app")
    await dashboard.client.post("/api/projects", json={"path": str(src)})

    resp = await dashboard.client.get("/api/projects/my-app")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["last_health_check_status"] == "pass"
    assert detail["last_health_check_at"] is not None
    assert detail["default_branch"] == "main"
