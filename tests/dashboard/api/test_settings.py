"""Tests for ``GET /api/settings`` — operator dashboard view.

Per `docs/v0-alignment-report.md` Plan #9 + presentation-plane spec
§ Settings view: surfaces heartbeats per active job, MCP server count,
per-project doctor + last-health-check, and per-project specialist
inventory counts. The operator hits this view to answer "what's
running, is it healthy, and what overrides are in place?"
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.settings import Settings
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig
from shared.models.job import JobConfig, JobState


def _project(root: Path, slug: str, repo: Path, *, doctor: str = "pass") -> None:
    repo.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig(
        slug=slug,
        name=slug,
        repo_path=str(repo),
        remote_url=f"https://github.com/example/{slug}",
        default_branch="main",
        created_at=datetime.now(UTC),
        last_health_check_at=datetime.now(UTC),
        last_health_check_status=doctor,  # type: ignore[arg-type]
    )
    atomic_write_json(paths.project_json(slug, root=root), project)


def _job(root: Path, slug: str, *, project_slug: str, state: JobState) -> None:
    cfg = JobConfig(
        job_id=f"jid-{slug}",
        job_slug=slug,
        project_slug=project_slug,
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="t",
        state=state,
    )
    atomic_write_json(paths.job_json(slug, root=root), cfg)


def _client(root: Path) -> TestClient:
    return TestClient(create_app(Settings(root=root, run_background_tasks=False)))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_get_settings_returns_runner_mode_and_cache_size(tmp_path: Path) -> None:
    root = tmp_path / "hammock-root"
    root.mkdir()
    with _client(root) as client:
        resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["runner_mode"] in ("fake", "real")
    assert isinstance(body["cache_size"], int)


def test_get_settings_lists_non_terminal_jobs(tmp_path: Path) -> None:
    """Active-jobs list includes jobs in SUBMITTED / STAGES_RUNNING /
    BLOCKED_ON_HUMAN; excludes terminal states."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    _project(root, "p", tmp_path / "p-repo")
    _job(root, "j-running", project_slug="p", state=JobState.STAGES_RUNNING)
    _job(root, "j-blocked", project_slug="p", state=JobState.BLOCKED_ON_HUMAN)
    _job(root, "j-done", project_slug="p", state=JobState.COMPLETED)
    _job(root, "j-failed", project_slug="p", state=JobState.FAILED)

    with _client(root) as client:
        body = client.get("/api/settings").json()
    active_slugs = {j["job_slug"] for j in body["active_jobs"]}
    assert active_slugs == {"j-running", "j-blocked"}


def test_get_settings_active_job_has_heartbeat_and_pid_fields(tmp_path: Path) -> None:
    root = tmp_path / "hammock-root"
    root.mkdir()
    _project(root, "p", tmp_path / "p-repo")
    _job(root, "j", project_slug="p", state=JobState.STAGES_RUNNING)

    with _client(root) as client:
        body = client.get("/api/settings").json()
    job = body["active_jobs"][0]
    # Shape: every entry has these keys (values may be None).
    for k in ("job_slug", "state", "heartbeat_age_seconds", "pid", "pid_alive"):
        assert k in job, f"missing key {k!r} in active_jobs entry: {job}"


def test_get_settings_lists_projects_with_doctor_status(tmp_path: Path) -> None:
    root = tmp_path / "hammock-root"
    root.mkdir()
    _project(root, "alpha", tmp_path / "alpha-repo", doctor="pass")
    _project(root, "beta", tmp_path / "beta-repo", doctor="warn")

    with _client(root) as client:
        body = client.get("/api/settings").json()
    projects = {p["slug"]: p for p in body["projects"]}
    assert projects["alpha"]["doctor_status"] == "pass"
    assert projects["beta"]["doctor_status"] == "warn"
    # last_health_check_at must round-trip as a parseable ISO 8601
    # timestamp (Codex review on PR #25 — guards against a future
    # serializer change silently dropping the field or losing tz).
    raw = projects["alpha"]["last_health_check_at"]
    assert isinstance(raw, str)
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_get_settings_specialist_inventory_counts_overrides(tmp_path: Path) -> None:
    """For each project, count agent + skill override files."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    repo = tmp_path / "p-repo"
    _project(root, "p", repo)
    # Drop two agent overrides + one skill override.
    agents = paths.project_agents_overrides(repo)
    agents.mkdir(parents=True)
    (agents / "writer.md").write_text(
        "---\nname: writer\ndescription: x\nmodel: claude-opus-4-7\n---\nbody"
    )
    (agents / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: y\nmodel: claude-opus-4-7\n---\nbody"
    )
    skills = paths.project_skills_overrides(repo)
    skills.mkdir(parents=True)
    (skills / "tdd.md").write_text(
        "---\nskill_id: tdd\ndescription: x\ntriggering_summary: y\n---\nbody"
    )

    with _client(root) as client:
        body = client.get("/api/settings").json()
    inv = body["inventory"]
    # Per-project counts
    assert inv["agents_per_project"]["p"] == 2
    assert inv["skills_per_project"]["p"] == 1
    # Aggregate
    assert inv["total_agent_overrides"] == 2
    assert inv["total_skill_overrides"] == 1


def test_get_settings_mcp_server_count_present(tmp_path: Path) -> None:
    """Even when no MCP servers are spawned, the key is present (0)."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    with _client(root) as client:
        body = client.get("/api/settings").json()
    assert body["mcp_server_count"] == 0
