"""Tests for the workflows CRUD endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_list_workflows_includes_bundled_fix_bug(client: TestClient) -> None:
    r = client.get("/api/workflows")
    assert r.status_code == 200
    names = [w["name"] for w in r.json()["workflows"]]
    assert "fix-bug" in names
    fix_bug = next(w for w in r.json()["workflows"] if w["name"] == "fix-bug")
    assert fix_bug["bundled"] is True


def test_workflow_detail_returns_yaml_source(client: TestClient) -> None:
    r = client.get("/api/workflows/fix-bug")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "fix-bug"
    assert body["bundled"] is True
    assert "name: fix-bug" in body["yaml"]
    assert isinstance(body["nodes"], list)
    assert any(n["id"] == "implement" for n in body["nodes"])


def test_workflow_detail_404(client: TestClient) -> None:
    r = client.get("/api/workflows/nonexistent")
    assert r.status_code == 404


def test_create_user_workflow(client: TestClient, hammock_root: Path) -> None:
    yaml_text = """
name: my-test
description: a test workflow
nodes:
  - id: a
    prompt: write-bug-report
"""
    r = client.post("/api/workflows", json={"name": "my-test", "yaml": yaml_text})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "my-test"
    # File landed in user dir
    user_path = hammock_root / "workflows" / "my-test.yaml"
    assert user_path.is_file()
    # Now appears in list
    names = [w["name"] for w in client.get("/api/workflows").json()["workflows"]]
    assert "my-test" in names


def test_create_rejects_duplicate_bundled_name(client: TestClient) -> None:
    yaml_text = """
name: fix-bug
nodes:
  - id: a
    prompt: foo
"""
    r = client.post("/api/workflows", json={"name": "fix-bug", "yaml": yaml_text})
    assert r.status_code == 409


def test_create_rejects_invalid_yaml(client: TestClient) -> None:
    r = client.post(
        "/api/workflows",
        json={"name": "bad-yaml", "yaml": "this: : not: valid: ::"},
    )
    assert r.status_code == 400


def test_create_rejects_yaml_name_mismatch(client: TestClient) -> None:
    yaml_text = """
name: different-name
nodes:
  - id: a
    prompt: foo
"""
    r = client.post(
        "/api/workflows",
        json={"name": "url-name", "yaml": yaml_text},
    )
    assert r.status_code == 400


def test_create_rejects_bad_url_name(client: TestClient) -> None:
    yaml_text = """
name: weird
nodes:
  - id: a
    prompt: foo
"""
    r = client.post(
        "/api/workflows",
        json={"name": "../etc/passwd", "yaml": yaml_text},
    )
    assert r.status_code == 400


def test_update_user_workflow(client: TestClient, hammock_root: Path) -> None:
    user_dir = hammock_root / "workflows"
    user_dir.mkdir(exist_ok=True)
    (user_dir / "u1.yaml").write_text(
        """
name: u1
nodes:
  - id: a
    prompt: foo
"""
    )
    new_yaml = """
name: u1
description: updated
nodes:
  - id: a
    prompt: foo
  - id: b
    prompt: bar
    after: [a]
"""
    r = client.put("/api/workflows/u1", json={"yaml": new_yaml})
    assert r.status_code == 200, r.text
    body = client.get("/api/workflows/u1").json()
    assert body["description"] == "updated"
    assert len(body["nodes"]) == 2


def test_update_bundled_rejected(client: TestClient) -> None:
    yaml_text = """
name: fix-bug
nodes:
  - id: a
    prompt: foo
"""
    r = client.put("/api/workflows/fix-bug", json={"yaml": yaml_text})
    assert r.status_code == 405


def test_update_404_user_workflow(client: TestClient) -> None:
    yaml_text = """
name: nope
nodes:
  - id: a
    prompt: foo
"""
    r = client.put("/api/workflows/nope", json={"yaml": yaml_text})
    assert r.status_code == 404


def test_delete_user_workflow(client: TestClient, hammock_root: Path) -> None:
    user_dir = hammock_root / "workflows"
    user_dir.mkdir(exist_ok=True)
    (user_dir / "delme.yaml").write_text(
        """
name: delme
nodes:
  - id: a
    prompt: foo
"""
    )
    r = client.delete("/api/workflows/delme")
    assert r.status_code == 200
    assert not (user_dir / "delme.yaml").is_file()


def test_delete_bundled_rejected(client: TestClient) -> None:
    r = client.delete("/api/workflows/fix-bug")
    assert r.status_code == 405


def test_validate_endpoint_accepts_good_yaml(client: TestClient) -> None:
    yaml_text = """
name: t
nodes:
  - id: a
    prompt: foo
"""
    r = client.post("/api/workflows/validate", json={"yaml": yaml_text})
    assert r.status_code == 200
    assert r.json()["valid"] is True


def test_validate_endpoint_rejects_bad_yaml(client: TestClient) -> None:
    r = client.post(
        "/api/workflows/validate",
        json={"yaml": "name: t\nnodes:\n  - id: a\n    prompt: foo\n    after: [missing]\n"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert "unknown" in body["error"].lower() or "missing" in body["error"].lower()


def test_user_workflow_usable_for_submit(client: TestClient, hammock_root: Path) -> None:
    """A user workflow can be used to submit a job (via fake runner)."""
    user_dir = hammock_root / "workflows"
    user_dir.mkdir(exist_ok=True)
    (user_dir / "user-flow.yaml").write_text(
        """
name: user-flow
nodes:
  - id: a
    prompt: write-bug-report
"""
    )
    r = client.post(
        "/api/jobs",
        json={"workflow": "user-flow", "request": "test"},
    )
    assert r.status_code == 200, r.text
