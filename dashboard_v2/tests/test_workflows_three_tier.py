"""Three-tier workflow visibility tests.

Bundled (read-only, ships with hammock) + custom (cross-project,
``<HAMMOCK_V2_ROOT>/workflows/``) + project-specific (under a registered
project's repo). Resolution priority at submit: project-specific >
custom > bundled.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _wf_yaml(name: str, description: str = "") -> str:
    desc = f"description: {description}\n" if description else ""
    return f"""
name: {name}
{desc}nodes:
  - id: a
    prompt: write-bug-report
"""


def _register_project(client: TestClient, slug: str, repo_path: Path) -> None:
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / ".git").mkdir(exist_ok=True)
    r = client.post(
        "/api/projects",
        json={"slug": slug, "repo_path": str(repo_path), "name": slug},
    )
    assert r.status_code in (200, 201), r.text


def _seed_project_workflow(repo_path: Path, name: str, description: str = "") -> None:
    wf_dir = repo_path / ".hammock-v2" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / f"{name}.yaml").write_text(_wf_yaml(name, description))


# -------------------- Aggregate list --------------------


def test_list_workflows_includes_bundled_with_source_field(client: TestClient) -> None:
    r = client.get("/api/workflows")
    assert r.status_code == 200
    bundled_entries = [w for w in r.json()["workflows"] if w["source"] == "bundled"]
    assert any(w["name"] == "fix-bug" for w in bundled_entries)
    fix_bug = next(w for w in bundled_entries if w["name"] == "fix-bug")
    # Back-compat field still present
    assert fix_bug["bundled"] is True
    assert fix_bug["source"] == "bundled"
    assert "node_count" in fix_bug


def test_list_workflows_includes_custom_after_create(
    client: TestClient, hammock_v2_root: Path
) -> None:
    r = client.post(
        "/api/workflows",
        json={"name": "team-flow", "yaml": _wf_yaml("team-flow", "shared by all projects")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "custom"
    listed = client.get("/api/workflows").json()["workflows"]
    custom = [w for w in listed if w["source"] == "custom"]
    names = [w["name"] for w in custom]
    assert "team-flow" in names
    # Lives under <root>/workflows/
    assert (hammock_v2_root / "workflows" / "team-flow.yaml").is_file()


def test_list_workflows_includes_project_specific(
    client: TestClient, tmp_path: Path
) -> None:
    repo = tmp_path / "team-app"
    _register_project(client, "team-app", repo)
    _seed_project_workflow(repo, "team-app-flow", "tied to this project")
    listed = client.get("/api/workflows").json()["workflows"]
    project_entries = [w for w in listed if w["source"] == "team-app"]
    assert [w["name"] for w in project_entries] == ["team-app-flow"]
    # bundled field is False on project-specific entries
    assert project_entries[0]["bundled"] is False


def test_list_workflows_three_sources_present(
    client: TestClient, hammock_v2_root: Path, tmp_path: Path
) -> None:
    """Aggregate list returns entries for all three sources."""
    # custom
    client.post(
        "/api/workflows",
        json={"name": "custom-flow", "yaml": _wf_yaml("custom-flow")},
    )
    # project-specific
    repo = tmp_path / "alpha"
    _register_project(client, "alpha", repo)
    _seed_project_workflow(repo, "alpha-flow")
    listed = client.get("/api/workflows").json()["workflows"]
    sources = {w["source"] for w in listed}
    assert "bundled" in sources
    assert "custom" in sources
    assert "alpha" in sources
    # Source-specific lookups work
    assert any(w["name"] == "fix-bug" and w["source"] == "bundled" for w in listed)
    assert any(w["name"] == "custom-flow" and w["source"] == "custom" for w in listed)
    assert any(w["name"] == "alpha-flow" and w["source"] == "alpha" for w in listed)


# -------------------- Per-project picker --------------------


def test_project_workflows_includes_custom_and_bundled(
    client: TestClient, hammock_v2_root: Path, tmp_path: Path
) -> None:
    """Picker on the job-submit form pulls bundled + custom + this
    project's project-specific. Cross-project workflows defined under
    OTHER projects must NOT leak in."""
    # custom (visible to every project)
    client.post(
        "/api/workflows",
        json={"name": "shared", "yaml": _wf_yaml("shared")},
    )
    # this project
    a_repo = tmp_path / "a"
    _register_project(client, "a", a_repo)
    _seed_project_workflow(a_repo, "a-only")
    # other project's workflow — must NOT show in project a's picker
    b_repo = tmp_path / "b"
    _register_project(client, "b", b_repo)
    _seed_project_workflow(b_repo, "b-only")

    listed = client.get("/api/projects/a/workflows").json()["workflows"]
    by_source: dict[str, list[str]] = {}
    for w in listed:
        by_source.setdefault(w["source"], []).append(w["name"])
    # Bundled present
    assert "fix-bug" in by_source["bundled"]
    # Custom present
    assert "shared" in by_source["custom"]
    # Project's own present
    assert "a-only" in by_source.get("a", [])
    # Other project's NOT present
    for entries in by_source.values():
        assert "b-only" not in entries


def test_project_workflows_shadowing(client: TestClient, tmp_path: Path) -> None:
    """When a name exists at multiple tiers, project-specific shadows
    custom shadows bundled in the per-project picker."""
    # custom shadows bundled name "fix-bug" via override
    client.post(
        "/api/workflows",
        json={"name": "shadow", "yaml": _wf_yaml("shadow", "from custom")},
    )
    a_repo = tmp_path / "a"
    _register_project(client, "a", a_repo)
    # add a project-specific copy with the same name
    _seed_project_workflow(a_repo, "shadow", "from project")

    listed = client.get("/api/projects/a/workflows").json()["workflows"]
    matches = [w for w in listed if w["name"] == "shadow"]
    # Only one entry (project-specific shadowed custom)
    assert len(matches) == 1
    assert matches[0]["source"] == "a"
    assert matches[0]["description"] == "from project"


# -------------------- Resolution at submit --------------------


def test_resolve_at_submit_project_specific_wins(
    hammock_v2_root: Path, tmp_path: Path
) -> None:
    """Direct unit test of the resolver: project-specific > custom > bundled."""
    from dashboard_v2 import projects as proj
    from dashboard_v2 import workflows as wf_lib

    repo = tmp_path / "alpha"
    repo.mkdir()
    (repo / ".git").mkdir()
    proj.write_project(slug="alpha", repo_path=repo, name="alpha", root=hammock_v2_root)

    # custom (HAMMOCK_V2_ROOT/workflows/dup.yaml)
    custom_dir = wf_lib.custom_workflows_dir(hammock_v2_root)
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "dup.yaml").write_text(_wf_yaml("dup", "from custom"))
    # project-specific (repo/.hammock-v2/workflows/dup.yaml)
    proj_wf_dir = wf_lib.project_workflows_dir(repo)
    proj_wf_dir.mkdir(parents=True, exist_ok=True)
    (proj_wf_dir / "dup.yaml").write_text(_wf_yaml("dup", "from project"))

    resolved = wf_lib.resolve_at_submit("dup", root=hammock_v2_root, project_slug="alpha")
    assert resolved is not None
    assert "from project" in resolved.read_text()


def test_resolve_at_submit_custom_when_no_project_copy(
    hammock_v2_root: Path, tmp_path: Path
) -> None:
    from dashboard_v2 import projects as proj
    from dashboard_v2 import workflows as wf_lib

    repo = tmp_path / "beta"
    repo.mkdir()
    (repo / ".git").mkdir()
    proj.write_project(slug="beta", repo_path=repo, name="beta", root=hammock_v2_root)

    custom_dir = wf_lib.custom_workflows_dir(hammock_v2_root)
    custom_dir.mkdir(parents=True, exist_ok=True)
    (custom_dir / "team-only.yaml").write_text(_wf_yaml("team-only", "from custom"))

    resolved = wf_lib.resolve_at_submit("team-only", root=hammock_v2_root, project_slug="beta")
    assert resolved is not None
    assert "from custom" in resolved.read_text()


def test_resolve_at_submit_falls_back_to_bundled(hammock_v2_root: Path) -> None:
    """Bundled is the last fallback when neither project nor custom has the name."""
    from dashboard_v2 import workflows as wf_lib

    resolved = wf_lib.resolve_at_submit("fix-bug", root=hammock_v2_root, project_slug=None)
    assert resolved is not None
    assert resolved.is_file()
    # Bundled workflows live under hammock_v2/workflows/
    assert "hammock_v2/workflows" in str(resolved)


def test_resolve_at_submit_returns_none_when_unknown(hammock_v2_root: Path) -> None:
    from dashboard_v2 import workflows as wf_lib

    assert wf_lib.resolve_at_submit("nonexistent-flow", root=hammock_v2_root, project_slug=None) is None


# -------------------- Custom CRUD permissions --------------------


def test_create_custom_rejects_bundled_name(client: TestClient) -> None:
    r = client.post(
        "/api/workflows",
        json={"name": "fix-bug", "yaml": _wf_yaml("fix-bug")},
    )
    assert r.status_code == 409


def test_delete_custom_works(client: TestClient, hammock_v2_root: Path) -> None:
    client.post(
        "/api/workflows",
        json={"name": "delme-custom", "yaml": _wf_yaml("delme-custom")},
    )
    r = client.delete("/api/workflows/delme-custom")
    assert r.status_code == 200
    assert r.json()["source"] == "custom"
    assert not (hammock_v2_root / "workflows" / "delme-custom.yaml").is_file()


def test_delete_bundled_blocked(client: TestClient) -> None:
    r = client.delete("/api/workflows/fix-bug")
    assert r.status_code == 405
