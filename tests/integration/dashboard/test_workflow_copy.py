"""Stage 6 — copy a bundled workflow into a project's .hammock/.

Per ``docs/hammock-workflow.md``: the operator forks a bundled workflow
into their project repo as
``<repo>/.hammock/workflows/<source>-<project_slug>/`` (default
suffix; can be renamed in-place by the operator). The folder contains
the bundled ``workflow.yaml`` plus the full ``prompts/`` subtree.

After copy, the new workflow surfaces in the per-project workflow
listing as ``source: custom`` and is selectable in the submit
dropdown.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.integration.conftest import DashboardHandle


def _git(args: list[str], *, cwd: Path) -> None:
    result = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"


def _init_repo(parent: Path, name: str) -> Path:
    repo = parent / name
    repo.mkdir()
    _git(["init", "-b", "main"], cwd=repo)
    _git(["remote", "add", "origin", "https://github.com/me/repo.git"], cwd=repo)
    _git(["config", "user.email", "t@example.com"], cwd=repo)
    _git(["config", "user.name", "t"], cwd=repo)
    (repo / "README.md").write_text("hi\n")
    _git(["add", "."], cwd=repo)
    _git(["commit", "-m", "init"], cwd=repo)
    _git(["update-ref", "refs/remotes/origin/HEAD", "refs/heads/main"], cwd=repo)
    _git(["symbolic-ref", "refs/remotes/origin/HEAD", "refs/heads/main"], cwd=repo)
    return repo


# ---------------------------------------------------------------------------
# POST /api/projects/{slug}/workflows/copy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_creates_destination_with_yaml_and_prompts(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Copying ``fix-bug`` into a project creates
    ``<repo>/.hammock/workflows/fix-bug-<slug>/`` with the bundled
    ``workflow.yaml`` and the full ``prompts/`` subtree intact."""
    src_repo = _init_repo(tmp_path_factory.mktemp("p"), "myapp")
    register = await dashboard.client.post("/api/projects", json={"path": str(src_repo)})
    assert register.status_code == 201, register.text
    slug = register.json()["project"]["slug"]

    resp = await dashboard.client.post(
        f"/api/projects/{slug}/workflows/copy",
        json={"source": "fix-bug"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Default destination name is <source>-<project_slug>.
    expected_dest = src_repo / ".hammock" / "workflows" / f"fix-bug-{slug}"
    assert body["destination"] == str(expected_dest)
    assert expected_dest.is_dir()
    assert (expected_dest / "workflow.yaml").is_file()

    # Prompts subtree carried over with at least one .md file
    # (bundled fix-bug has 10 agent-actor nodes).
    prompts = expected_dest / "prompts"
    assert prompts.is_dir()
    md_files = sorted(p.name for p in prompts.glob("*.md"))
    assert "implement.md" in md_files
    assert "write-bug-report.md" in md_files

    # The workflow.yaml content is byte-equivalent to the bundled one.
    bundled_yaml = (
        Path(__file__).parent.parent.parent.parent
        / "hammock"
        / "templates"
        / "workflows"
        / "fix-bug"
        / "workflow.yaml"
    )
    assert (expected_dest / "workflow.yaml").read_bytes() == bundled_yaml.read_bytes()


@pytest.mark.asyncio
async def test_copy_response_carries_workflow_item(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Response body returns the new ProjectWorkflowItem so the UI can
    refresh the listing without a separate round-trip."""
    src = _init_repo(tmp_path_factory.mktemp("p"), "responsetest")
    register = await dashboard.client.post("/api/projects", json={"path": str(src)})
    slug = register.json()["project"]["slug"]

    resp = await dashboard.client.post(
        f"/api/projects/{slug}/workflows/copy",
        json={"source": "fix-bug"},
    )
    assert resp.status_code == 201, resp.text
    item = resp.json()["workflow"]
    assert item["job_type"] == f"fix-bug-{slug}"
    assert item["source"] == "custom"
    assert item["valid"] is True


@pytest.mark.asyncio
async def test_copy_with_explicit_dest_name(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Operator can override the default dest_name for cases where
    they want a more memorable folder name (e.g. fix-bug-strict)."""
    src = _init_repo(tmp_path_factory.mktemp("p"), "explicitname")
    register = await dashboard.client.post("/api/projects", json={"path": str(src)})
    slug = register.json()["project"]["slug"]

    resp = await dashboard.client.post(
        f"/api/projects/{slug}/workflows/copy",
        json={"source": "fix-bug", "dest_name": "fix-bug-strict"},
    )
    assert resp.status_code == 201, resp.text
    expected = src / ".hammock" / "workflows" / "fix-bug-strict"
    assert expected.is_dir()
    assert (expected / "workflow.yaml").is_file()


@pytest.mark.asyncio
async def test_copy_to_existing_destination_returns_409(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Calling copy twice with the same dest_name is a 409 — the
    operator must delete the existing folder first or pick a
    different dest_name (we don't silently overwrite)."""
    src = _init_repo(tmp_path_factory.mktemp("p"), "twice")
    register = await dashboard.client.post("/api/projects", json={"path": str(src)})
    slug = register.json()["project"]["slug"]

    first = await dashboard.client.post(
        f"/api/projects/{slug}/workflows/copy",
        json={"source": "fix-bug"},
    )
    assert first.status_code == 201

    second = await dashboard.client.post(
        f"/api/projects/{slug}/workflows/copy",
        json={"source": "fix-bug"},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_copy_unknown_source_returns_404(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    src = _init_repo(tmp_path_factory.mktemp("p"), "unknown")
    register = await dashboard.client.post("/api/projects", json={"path": str(src)})
    slug = register.json()["project"]["slug"]

    resp = await dashboard.client.post(
        f"/api/projects/{slug}/workflows/copy",
        json={"source": "nonexistent-workflow"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_copy_unknown_project_returns_404(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    resp = await dashboard.client.post(
        "/api/projects/nope/workflows/copy",
        json={"source": "fix-bug"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Round-trip: after copy, the new workflow is listed and selectable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copied_workflow_appears_in_listing(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    src = _init_repo(tmp_path_factory.mktemp("p"), "roundtrip")
    register = await dashboard.client.post("/api/projects", json={"path": str(src)})
    slug = register.json()["project"]["slug"]

    # Before copy: only bundled.
    pre = await dashboard.client.get(f"/api/projects/{slug}/workflows")
    pre_custom = [i for i in pre.json() if i["source"] == "custom"]
    assert pre_custom == []

    # Copy.
    copy = await dashboard.client.post(
        f"/api/projects/{slug}/workflows/copy",
        json={"source": "fix-bug"},
    )
    assert copy.status_code == 201

    # After copy: the new entry is listed as custom and valid.
    post = await dashboard.client.get(f"/api/projects/{slug}/workflows")
    post_custom = [i for i in post.json() if i["source"] == "custom"]
    assert len(post_custom) == 1
    assert post_custom[0]["job_type"] == f"fix-bug-{slug}"
    assert post_custom[0]["valid"] is True
