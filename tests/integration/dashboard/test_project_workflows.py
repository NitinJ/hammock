"""Stage 5 — project-local workflow discovery.

Per ``docs/hammock-workflow.md``: a registered project's repo can carry
custom workflows under ``<repo>/.hammock/workflows/<name>/workflow.yaml``
with sibling ``prompts/<node_id>.md``. The dashboard surfaces these
alongside the bundled set per project, the compiler resolves
project-local before bundled, and verification rejects malformed
project workflows with a context-rich error.
"""

from __future__ import annotations

import json
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


def _seed_project_workflow(
    repo: Path,
    name: str,
    *,
    workflow_yaml: str,
    prompt_files: dict[str, str] | None = None,
) -> Path:
    """Create ``<repo>/.hammock/workflows/<name>/`` with a workflow.yaml
    and optional prompts. Returns the workflow folder path."""
    folder = repo / ".hammock" / "workflows" / name
    folder.mkdir(parents=True)
    (folder / "workflow.yaml").write_text(workflow_yaml)
    if prompt_files:
        prompts_dir = folder / "prompts"
        prompts_dir.mkdir()
        for node_id, content in prompt_files.items():
            (prompts_dir / f"{node_id}.md").write_text(content)
    return folder


_VALID_WORKFLOW_YAML = """\
schema_version: 1
workflow: my-fix-bug
variables:
  request: { type: job-request }
  bug_report: { type: bug-report }
nodes:
  - id: write-bug-report
    kind: artifact
    actor: agent
    inputs: { request: $request }
    outputs: { bug_report: $bug_report }
"""


# ---------------------------------------------------------------------------
# GET /api/projects/{slug}/workflows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_with_no_local_workflows_lists_bundled_only(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A registered project without ``.hammock/workflows/`` returns
    just the bundled set. The endpoint is per-project so the dropdown
    in the UI is always project-aware."""
    src = _init_repo(tmp_path_factory.mktemp("p"), "no-custom")
    register = await dashboard.client.post("/api/projects", json={"path": str(src)})
    assert register.status_code == 201, register.text
    slug = register.json()["project"]["slug"]

    resp = await dashboard.client.get(f"/api/projects/{slug}/workflows")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    sources = {item["source"] for item in items}
    assert sources == {"bundled"}, f"expected only bundled, got {sources}"
    # fix-bug ships bundled.
    assert any(item["job_type"] == "fix-bug" for item in items)


@pytest.mark.asyncio
async def test_project_local_workflow_surfaces_alongside_bundled(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A project with ``.hammock/workflows/my-custom/`` is listed with
    ``source: custom`` alongside the bundled set."""
    src = _init_repo(tmp_path_factory.mktemp("p"), "with-custom")
    _seed_project_workflow(
        src,
        "my-custom",
        workflow_yaml=_VALID_WORKFLOW_YAML,
        prompt_files={"write-bug-report": "Custom task instruction.\n"},
    )
    register = await dashboard.client.post("/api/projects", json={"path": str(src)})
    slug = register.json()["project"]["slug"]

    resp = await dashboard.client.get(f"/api/projects/{slug}/workflows")
    assert resp.status_code == 200, resp.text
    items = resp.json()

    custom = [i for i in items if i["source"] == "custom"]
    assert len(custom) == 1
    assert custom[0]["job_type"] == "my-custom"
    assert custom[0]["workflow_name"] == "my-fix-bug"
    assert custom[0]["valid"] is True

    # Bundled fix-bug still listed.
    assert any(i["job_type"] == "fix-bug" and i["source"] == "bundled" for i in items)


@pytest.mark.asyncio
async def test_project_local_workflow_missing_prompt_marked_invalid(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Verification chokepoint: a project workflow with an agent-actor
    node whose prompts/<node_id>.md is missing is listed as invalid
    with a context-rich error. The dashboard hides invalid ones from
    the submit dropdown but lists them here so the user can fix."""
    src = _init_repo(tmp_path_factory.mktemp("p"), "broken-prompt")
    _seed_project_workflow(
        src,
        "broken",
        workflow_yaml=_VALID_WORKFLOW_YAML,
        prompt_files=None,  # no prompts/ at all
    )
    register = await dashboard.client.post("/api/projects", json={"path": str(src)})
    slug = register.json()["project"]["slug"]

    resp = await dashboard.client.get(f"/api/projects/{slug}/workflows")
    items = resp.json()
    broken = next((i for i in items if i["job_type"] == "broken"), None)
    assert broken is not None
    assert broken["valid"] is False
    assert broken["error"] is not None
    assert "write-bug-report" in broken["error"]


@pytest.mark.asyncio
async def test_project_local_workflow_missing_schema_version_marked_invalid(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """A workflow yaml without ``schema_version: 1`` is listed as
    invalid (not silently ignored). Stage 4's loader is the source of
    truth; Stage 5 surfaces the error in the UI."""
    src = _init_repo(tmp_path_factory.mktemp("p"), "no-version")
    bad_yaml = _VALID_WORKFLOW_YAML.replace("schema_version: 1\n", "")
    _seed_project_workflow(
        src,
        "no-version",
        workflow_yaml=bad_yaml,
        prompt_files={"write-bug-report": "x"},
    )
    register = await dashboard.client.post("/api/projects", json={"path": str(src)})
    slug = register.json()["project"]["slug"]

    resp = await dashboard.client.get(f"/api/projects/{slug}/workflows")
    items = resp.json()
    bad = next((i for i in items if i["job_type"] == "no-version"), None)
    assert bad is not None
    assert bad["valid"] is False
    assert bad["error"] and "schema_version" in bad["error"]


@pytest.mark.asyncio
async def test_unknown_project_returns_404(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    resp = await dashboard.client.get("/api/projects/does-not-exist/workflows")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Compile resolution: project-local wins over bundled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compile_resolves_project_local_workflow_over_bundled(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """When a project has ``.hammock/workflows/fix-bug/`` and the user
    submits a job with ``job_type: fix-bug``, the compiler picks the
    project-local copy — not the bundled one. The job's persisted
    workflow_path points inside the project repo's .hammock/."""
    from unittest.mock import AsyncMock, patch

    src = _init_repo(tmp_path_factory.mktemp("p"), "shadow")
    _seed_project_workflow(
        src,
        "fix-bug",  # same name as bundled
        workflow_yaml=_VALID_WORKFLOW_YAML,
        prompt_files={"write-bug-report": "Custom override."},
    )
    register = await dashboard.client.post("/api/projects", json={"path": str(src)})
    slug = register.json()["project"]["slug"]

    async def _fake_spawn(job_slug: str, **kwargs: object) -> int:
        return 99999

    with patch("dashboard.api.jobs.spawn_driver", new=AsyncMock(side_effect=_fake_spawn)):
        resp = await dashboard.client.post(
            "/api/jobs",
            json={
                "project_slug": slug,
                "job_type": "fix-bug",
                "title": "smoke",
                "request_text": "x",
                "dry_run": False,
            },
        )
    assert resp.status_code == 201, resp.text
    job_slug = resp.json()["job_slug"]

    # The persisted workflow_path on the job must point inside the
    # project's .hammock/, NOT the bundled location.
    cfg_path = dashboard.root / "jobs" / job_slug / "job.json"
    cfg = json.loads(cfg_path.read_text())
    wf_path = Path(cfg["workflow_path"])
    assert ".hammock/workflows/fix-bug/workflow.yaml" in str(wf_path), (
        f"expected project-local resolution; got {wf_path}"
    )
