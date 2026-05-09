"""Tests for per-project workflows + prompts CRUD + project picker submit."""

from __future__ import annotations

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


_SAMPLE_YAML = """\
name: my-flow
description: Test
nodes:
  - id: only-node
    prompt: write-bug-report
"""


def _register(client: TestClient, tmp_path: Path, slug: str) -> Path:
    repo = tmp_path / slug
    _make_git_repo(repo)
    r = client.post("/api/projects", json={"repo_path": str(repo), "slug": slug})
    assert r.status_code == 201, r.text
    return repo


def test_list_project_workflows_returns_bundled(client: TestClient, tmp_path: Path) -> None:
    _register(client, tmp_path, "p1")
    r = client.get("/api/projects/p1/workflows")
    assert r.status_code == 200
    names = [w["name"] for w in r.json()["workflows"]]
    # Bundled fix-bug should always be visible
    assert "fix-bug" in names


def test_create_project_workflow(client: TestClient, tmp_path: Path) -> None:
    repo = _register(client, tmp_path, "p2")
    r = client.post(
        "/api/projects/p2/workflows",
        json={"name": "my-flow", "yaml": _SAMPLE_YAML},
    )
    assert r.status_code == 201, r.text
    target = repo / ".hammock-v2" / "workflows" / "my-flow.yaml"
    assert target.is_file()
    # Now appears in project list as non-bundled
    listing = client.get("/api/projects/p2/workflows").json()["workflows"]
    found = next(w for w in listing if w["name"] == "my-flow")
    assert found["bundled"] is False


def test_create_project_workflow_rejects_invalid_yaml(client: TestClient, tmp_path: Path) -> None:
    _register(client, tmp_path, "p3")
    r = client.post(
        "/api/projects/p3/workflows",
        json={"name": "broken", "yaml": "not a workflow"},
    )
    assert r.status_code == 400


def test_update_project_workflow(client: TestClient, tmp_path: Path) -> None:
    _register(client, tmp_path, "p4")
    client.post("/api/projects/p4/workflows", json={"name": "my-flow", "yaml": _SAMPLE_YAML})
    new_yaml = _SAMPLE_YAML.replace("Test", "Updated description")
    r = client.put("/api/projects/p4/workflows/my-flow", json={"yaml": new_yaml})
    assert r.status_code == 200


def test_delete_project_workflow(client: TestClient, tmp_path: Path) -> None:
    _register(client, tmp_path, "p5")
    client.post("/api/projects/p5/workflows", json={"name": "my-flow", "yaml": _SAMPLE_YAML})
    r = client.delete("/api/projects/p5/workflows/my-flow")
    assert r.status_code == 200
    r2 = client.delete("/api/projects/p5/workflows/my-flow")
    assert r2.status_code == 404


def test_get_project_workflow_falls_back_to_bundled(client: TestClient, tmp_path: Path) -> None:
    _register(client, tmp_path, "p6")
    r = client.get("/api/projects/p6/workflows/fix-bug")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "fix-bug"
    assert body["bundled"] is True


def test_list_project_prompts_returns_bundled(client: TestClient, tmp_path: Path) -> None:
    _register(client, tmp_path, "p7")
    r = client.get("/api/projects/p7/prompts")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()["prompts"]]
    # Bundled prompts include the standard set
    assert "write-bug-report" in names


def test_save_and_get_project_prompt(client: TestClient, tmp_path: Path) -> None:
    repo = _register(client, tmp_path, "p8")
    r = client.post(
        "/api/projects/p8/prompts",
        json={"name": "my-prompt", "content": "# Custom prompt\n\nDo the thing."},
    )
    assert r.status_code == 201
    assert (repo / ".hammock-v2" / "prompts" / "my-prompt.md").is_file()
    r2 = client.get("/api/projects/p8/prompts/my-prompt")
    assert r2.status_code == 200
    assert r2.json()["content"].startswith("# Custom prompt")


def test_submit_with_project_slug_clones_repo(
    client: TestClient, tmp_path: Path, hammock_root: Path
) -> None:
    repo = _register(client, tmp_path, "p9")
    # Add a custom workflow that shadows the bundled one
    r = client.post(
        "/api/jobs",
        json={"workflow": "fix-bug", "request": "test request", "project_slug": "p9"},
    )
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    job_repo = hammock_root / "jobs" / slug / "repo"
    assert job_repo.is_dir(), "project repo should be cloned into job dir"
    assert (job_repo / "README.md").is_file()
    _ = repo  # silence unused


def test_submit_rejects_unknown_project_slug(client: TestClient, hammock_root: Path) -> None:
    r = client.post(
        "/api/jobs",
        json={"workflow": "fix-bug", "request": "x", "project_slug": "nope"},
    )
    assert r.status_code == 400
    assert "not registered" in r.json()["detail"]
    _ = hammock_root


def test_project_workflow_shadows_bundled_at_submit(
    client: TestClient, tmp_path: Path, hammock_root: Path
) -> None:
    repo = _register(client, tmp_path, "p10")
    # Create a project-local workflow named fix-bug that overrides bundled.
    custom_yaml = """\
name: fix-bug
description: project-overridden
nodes:
  - id: only
    prompt: write-bug-report
"""
    # Save by writing directly (POST /workflows would 409 because bundled
    # name conflict is checked at the global workflows route).
    target = repo / ".hammock-v2" / "workflows" / "fix-bug.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(custom_yaml)
    r = client.post(
        "/api/jobs",
        json={"workflow": "fix-bug", "request": "x", "project_slug": "p10"},
    )
    assert r.status_code == 200, r.text
    slug = r.json()["slug"]
    snapshot = (hammock_root / "jobs" / slug / "workflow.yaml").read_text()
    assert "project-overridden" in snapshot
