"""``GET /api/settings`` — operator dashboard rollup.

Per `docs/v0-alignment-report.md` Plan #9 + presentation-plane spec
§ Settings view: surfaces what the operator needs to answer "is it
healthy, what's running, what overrides apply?" without tail-f-ing
log files.

v0 fields:

- ``runner_mode`` / ``claude_binary`` — mirrors ``/api/health``.
- ``cache_size`` — total cache entries.
- ``active_jobs`` — non-terminal jobs with heartbeat age + pid liveness.
- ``projects`` — registered projects with last doctor status + timestamp.
- ``inventory`` — per-project agent + skill override counts.
- ``mcp_server_count`` — currently-spawned MCP server descriptors.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from shared import paths
from shared.models.job import JobConfig, JobState

router = APIRouter(prefix="/api/settings", tags=["settings"])

_NON_TERMINAL_JOB_STATES = frozenset(
    {JobState.SUBMITTED, JobState.STAGES_RUNNING, JobState.BLOCKED_ON_HUMAN}
)


class ActiveJob(BaseModel):
    job_slug: str
    state: str
    heartbeat_age_seconds: float | None
    pid: int | None
    pid_alive: bool


class ProjectStatus(BaseModel):
    slug: str
    doctor_status: str | None
    last_health_check_at: datetime | None


class Inventory(BaseModel):
    agents_per_project: dict[str, int]
    skills_per_project: dict[str, int]
    total_agent_overrides: int
    total_skill_overrides: int


class SettingsResponse(BaseModel):
    runner_mode: str
    claude_binary: str | None
    cache_size: int
    active_jobs: list[ActiveJob]
    projects: list[ProjectStatus]
    inventory: Inventory
    mcp_server_count: int


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pid(pid_path: Path) -> int | None:
    try:
        return int(pid_path.read_text().strip())
    except (FileNotFoundError, ValueError, OSError):
        return None


def _heartbeat_age_seconds(hb_path: Path) -> float | None:
    if not hb_path.exists():
        return None
    try:
        return max(0.0, time.time() - hb_path.stat().st_mtime)
    except OSError:
        return None


def _list_active_jobs(root: Path | None) -> list[ActiveJob]:
    out: list[ActiveJob] = []
    jobs_dir = paths.jobs_dir(root=root)
    if not jobs_dir.is_dir():
        return out
    for entry in sorted(jobs_dir.iterdir()):
        if not entry.is_dir():
            continue
        slug = entry.name
        try:
            cfg = JobConfig.model_validate_json(paths.job_json(slug, root=root).read_text())
        except (FileNotFoundError, ValueError, OSError):
            continue
        if cfg.state not in _NON_TERMINAL_JOB_STATES:
            continue
        pid = _read_pid(paths.job_driver_pid(slug, root=root))
        out.append(
            ActiveJob(
                job_slug=slug,
                state=str(cfg.state),
                heartbeat_age_seconds=_heartbeat_age_seconds(paths.job_heartbeat(slug, root=root)),
                pid=pid,
                pid_alive=_pid_alive(pid),
            )
        )
    return out


def _list_projects(root: Path | None) -> list[ProjectStatus]:
    from shared.models import ProjectConfig

    out: list[ProjectStatus] = []
    projects_dir = paths.projects_dir(root=root)
    if not projects_dir.is_dir():
        return out
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        try:
            project = ProjectConfig.model_validate_json(
                paths.project_json(entry.name, root=root).read_text()
            )
        except (FileNotFoundError, ValueError, OSError):
            continue
        out.append(
            ProjectStatus(
                slug=project.slug,
                doctor_status=project.last_health_check_status,
                last_health_check_at=project.last_health_check_at,
            )
        )
    return out


def _build_inventory(root: Path | None) -> Inventory:
    from shared.models import ProjectConfig

    agents_per_project: dict[str, int] = {}
    skills_per_project: dict[str, int] = {}

    projects_dir = paths.projects_dir(root=root)
    if projects_dir.is_dir():
        for entry in sorted(projects_dir.iterdir()):
            if not entry.is_dir():
                continue
            try:
                project = ProjectConfig.model_validate_json(
                    paths.project_json(entry.name, root=root).read_text()
                )
            except (FileNotFoundError, ValueError, OSError):
                continue
            repo = Path(project.repo_path)
            agents_dir = paths.project_agents_overrides(repo)
            skills_dir = paths.project_skills_overrides(repo)
            agents_per_project[project.slug] = (
                len(list(agents_dir.glob("*.md"))) if agents_dir.is_dir() else 0
            )
            skills_per_project[project.slug] = (
                len(list(skills_dir.glob("*.md"))) if skills_dir.is_dir() else 0
            )

    return Inventory(
        agents_per_project=agents_per_project,
        skills_per_project=skills_per_project,
        total_agent_overrides=sum(agents_per_project.values()),
        total_skill_overrides=sum(skills_per_project.values()),
    )


def _mcp_server_count(app_state: Any) -> int:
    mgr = getattr(app_state, "mcp_manager", None)
    if mgr is None:
        return 0
    live = getattr(mgr, "_live", None)
    return len(live) if live is not None else 0


@router.get("", response_model=SettingsResponse)
async def get_settings(request: Request) -> SettingsResponse:
    cache = request.app.state.cache  # type: ignore[attr-defined]
    settings = request.app.state.settings  # type: ignore[attr-defined]
    return SettingsResponse(
        runner_mode=settings.runner_mode,
        claude_binary=(settings.claude_binary if settings.runner_mode == "real" else None),
        cache_size=sum(cache.size().values()),
        active_jobs=_list_active_jobs(settings.root),
        projects=_list_projects(settings.root),
        inventory=_build_inventory(settings.root),
        mcp_server_count=_mcp_server_count(request.app.state),
    )
