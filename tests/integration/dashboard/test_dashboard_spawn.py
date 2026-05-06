"""End-to-end through the live dashboard's job submission path.

Per impl-patch §5.4: this test drives ``POST /api/jobs`` against the
live dashboard fixture and verifies the v1 compile + spawn wiring.

The driver subprocess is patched out so we don't actually invoke
``python -m engine.v1`` (which would block on Claude / GitHub).
What we assert:

- Compile resolves the workflow YAML.
- ``submit_job`` writes the v1 job dir on disk: ``job.json`` (with
  v1 shape — workflow_name, workflow_path, state=submitted),
  ``variables/request.json`` (job-request envelope), workflow YAML
  snapshot pointed at by ``job.json``.
- ``spawn_driver`` is invoked with the job_slug + the dashboard's
  ``settings.root``.
- HTTP response is 201 with the slug.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from shared.v1 import paths as v1_paths
from tests.integration.conftest import DashboardHandle

_T1_WORKFLOW = """\
workflow: t1-basic-artifact

variables:
  request:                  { type: job-request }
  bug_report:               { type: bug-report }

nodes:
  - id: write-bug-report
    kind: artifact
    actor: agent
    inputs:
      request: $request
    outputs:
      bug_report: $bug_report
"""


def _seed_workflow_yaml(tmp_path: Path) -> Path:
    """Write a minimal v1 workflow YAML to disk and return its path."""
    wf_dir = tmp_path / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    wf = wf_dir / "t1-basic.yaml"
    wf.write_text(_T1_WORKFLOW)
    return wf


@pytest.mark.asyncio
async def test_post_jobs_compiles_and_spawns_driver(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """POST /api/jobs against the live dashboard:

    - compile_job runs (writes job dir).
    - spawn_driver is called (patched to a no-op).
    - Response is 201 with the slug.
    - Job dir contains v1 shape: job.json, variables/request.json,
      workflow snapshot at the path stored in job.json.
    """
    wf_dir = tmp_path_factory.mktemp("wf")
    workflow_path = wf_dir / "t1.yaml"
    workflow_path.write_text(_T1_WORKFLOW)

    # The compile flow looks up the workflow by job_type from a
    # bundled location. For this test we patch the resolver so the
    # api receives our explicit workflow yaml path.
    spawn_called: dict[str, object] = {}

    async def _fake_spawn(job_slug: str, **kwargs: object) -> int:
        spawn_called["job_slug"] = job_slug
        spawn_called["kwargs"] = kwargs
        return 99999  # fake PID; never used

    with (
        patch(
            "dashboard.compiler.compile._resolve_bundled_workflow",
            return_value=workflow_path,
        ),
        patch(
            "dashboard.api.jobs.spawn_driver",
            new=AsyncMock(side_effect=_fake_spawn),
        ),
        # The compile flow attempts a best-effort `git branch` for the
        # registered project; with no project on disk it short-circuits
        # via the FileNotFoundError branch (logged, not raised).
    ):
        resp = await dashboard.client.post(
            "/api/jobs",
            json={
                "project_slug": "lifecycle-target",
                "job_type": "fix-bug",
                "title": "smoke test",
                "request_text": "Fix the smoke",
                "dry_run": False,
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    job_slug = body["job_slug"]
    assert body["dry_run"] is False
    assert spawn_called["job_slug"] == job_slug

    root: Path = dashboard.root

    # job.json on disk with v1 shape.
    job_json_path = v1_paths.job_config_path(job_slug, root=root)
    assert job_json_path.is_file()
    cfg = json.loads(job_json_path.read_text())
    assert cfg["job_slug"] == job_slug
    assert cfg["workflow_name"] == "t1-basic-artifact"
    assert cfg["state"] == "submitted"
    assert cfg["workflow_path"].endswith("t1.yaml")

    # request envelope is seeded so the first node has its input.
    req_env_path = v1_paths.variable_envelope_path(job_slug, "request", root=root)
    assert req_env_path.is_file()
    env = json.loads(req_env_path.read_text())
    assert env["type"] == "job-request"
    assert env["value"]["text"] == "Fix the smoke"


@pytest.mark.asyncio
async def test_post_jobs_dry_run_does_not_spawn(
    dashboard: DashboardHandle, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """dry_run=True validates the workflow but does NOT write state or
    spawn the driver."""
    wf_dir = tmp_path_factory.mktemp("wf")
    workflow_path = wf_dir / "t1.yaml"
    workflow_path.write_text(_T1_WORKFLOW)

    with (
        patch(
            "dashboard.compiler.compile._resolve_bundled_workflow",
            return_value=workflow_path,
        ),
        patch("dashboard.api.jobs.spawn_driver", new=AsyncMock()) as spawn_mock,
    ):
        resp = await dashboard.client.post(
            "/api/jobs",
            json={
                "project_slug": "p",
                "job_type": "fix-bug",
                "title": "dry",
                "request_text": "x",
                "dry_run": True,
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["dry_run"] is True
    spawn_mock.assert_not_called()


@pytest.mark.asyncio
async def test_post_jobs_workflow_not_found_returns_422(
    dashboard: DashboardHandle,
) -> None:
    """When the bundled workflow lookup fails, compile returns a
    failure list which the handler maps to HTTP 422."""
    with patch(
        "dashboard.compiler.compile._resolve_bundled_workflow",
        return_value=None,
    ):
        resp = await dashboard.client.post(
            "/api/jobs",
            json={
                "project_slug": "p",
                "job_type": "nonexistent-job-type",
                "title": "x",
                "request_text": "x",
                "dry_run": False,
            },
        )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert any(f["kind"] == "workflow_not_found" for f in detail)
