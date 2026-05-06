"""Pure-function projections — disk → response payload.

Per impl-patch §Stage 3: every projection is a pure function of
``(root: Path, ...) → response``. No in-memory cache, no side effects.

The dashboard's HTTP handlers call these to build response bodies; the
SSE handler subscribes to filesystem changes via ``dashboard/watcher/``
and resolves each fan-out by calling the relevant projection again.

All paths come from ``shared.v1.paths``. The disk layout is the v1
layout exclusively — v0 is gone.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from shared.v1 import paths as v1_paths
from shared.v1.envelope import Envelope
from shared.v1.job import JobConfig, JobState, NodeRun, NodeRunState

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ProjectListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    name: str
    repo_path: str
    open_jobs: int
    last_job_at: datetime | None


class ProjectDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    name: str
    repo_path: str
    remote_url: str
    default_branch: str


class JobListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_slug: str
    workflow_name: str
    state: JobState
    submitted_at: datetime
    updated_at: datetime
    repo_slug: str | None = None


class NodeListEntry(BaseModel):
    """One entry in the job-detail node list."""

    model_config = ConfigDict(extra="forbid")
    node_id: str
    state: NodeRunState
    attempts: int
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class JobDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_slug: str
    workflow_name: str
    workflow_path: str
    state: JobState
    submitted_at: datetime
    updated_at: datetime
    repo_slug: str | None = None
    nodes: list[NodeListEntry] = Field(default_factory=list)


class NodeDetail(BaseModel):
    """Per-node drilldown response."""

    model_config = ConfigDict(extra="forbid")
    node_id: str
    state: NodeRunState
    attempts: int
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # Output envelopes the node produced (top-level only here; loop-
    # indexed envelopes surface via the loop's parent node).
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)


class HilQueueItem(BaseModel):
    """One pending HIL gate awaiting human input."""

    model_config = ConfigDict(extra="forbid")
    job_slug: str
    node_id: str
    output_var_names: list[str]
    output_types: dict[str, str]
    presentation: dict[str, Any]
    loop_id: str | None = None
    iteration: int | None = None
    created_at: datetime | None = None


# ---------------------------------------------------------------------------
# Project projections
# ---------------------------------------------------------------------------


def project_list(root: Path) -> list[ProjectListItem]:
    """Enumerate projects on disk."""
    projects_dir = v1_paths.jobs_dir(root=root).parent / "projects"
    if not projects_dir.is_dir():
        return []
    items: list[ProjectListItem] = []
    for p in sorted(projects_dir.iterdir()):
        item = project_list_item(root, p.name)
        if item is not None:
            items.append(item)
    return items


def project_list_item(root: Path, slug: str) -> ProjectListItem | None:
    pj = root / "projects" / slug / "project.json"
    if not pj.is_file():
        return None
    data = json.loads(pj.read_text())
    open_count = 0
    last_job: datetime | None = None
    for cfg in _iter_jobs(root):
        if cfg.repo_slug == slug or _project_slug_for_repo(data, cfg) == slug:
            if cfg.state in {JobState.SUBMITTED, JobState.RUNNING, JobState.BLOCKED_ON_HUMAN}:
                open_count += 1
            if last_job is None or cfg.updated_at > last_job:
                last_job = cfg.updated_at
    return ProjectListItem(
        slug=slug,
        name=data.get("name", slug),
        repo_path=data.get("repo_path", ""),
        open_jobs=open_count,
        last_job_at=last_job,
    )


def project_detail(root: Path, slug: str) -> ProjectDetail | None:
    pj = root / "projects" / slug / "project.json"
    if not pj.is_file():
        return None
    data = json.loads(pj.read_text())
    return ProjectDetail(
        slug=slug,
        name=data.get("name", slug),
        repo_path=data.get("repo_path", ""),
        remote_url=data.get("remote_url", ""),
        default_branch=data.get("default_branch", "main"),
    )


def _project_slug_for_repo(project_data: dict[str, Any], job: JobConfig) -> str | None:
    return None  # repo_slug pinning could be added later; KISS.


# ---------------------------------------------------------------------------
# Job projections
# ---------------------------------------------------------------------------


def _iter_jobs(root: Path) -> list[JobConfig]:
    jobs_dir = v1_paths.jobs_dir(root=root)
    if not jobs_dir.is_dir():
        return []
    out: list[JobConfig] = []
    for jd in sorted(jobs_dir.iterdir()):
        cfg_path = jd / "job.json"
        if not cfg_path.is_file():
            continue
        try:
            out.append(JobConfig.model_validate_json(cfg_path.read_text()))
        except Exception:
            continue
    return out


def job_list(
    root: Path,
    *,
    repo_slug: str | None = None,
    state: JobState | None = None,
) -> list[JobListItem]:
    out: list[JobListItem] = []
    for cfg in _iter_jobs(root):
        if repo_slug is not None and cfg.repo_slug != repo_slug:
            continue
        if state is not None and cfg.state != state:
            continue
        out.append(_to_job_list_item(cfg))
    return out


def _to_job_list_item(cfg: JobConfig) -> JobListItem:
    return JobListItem(
        job_slug=cfg.job_slug,
        workflow_name=cfg.workflow_name,
        state=cfg.state,
        submitted_at=cfg.submitted_at,
        updated_at=cfg.updated_at,
        repo_slug=cfg.repo_slug,
    )


def job_list_item(root: Path, job_slug: str) -> JobListItem | None:
    cfg = _read_job_config(root, job_slug)
    if cfg is None:
        return None
    return _to_job_list_item(cfg)


def job_detail(root: Path, job_slug: str) -> JobDetail | None:
    cfg = _read_job_config(root, job_slug)
    if cfg is None:
        return None
    return JobDetail(
        job_slug=cfg.job_slug,
        workflow_name=cfg.workflow_name,
        workflow_path=cfg.workflow_path,
        state=cfg.state,
        submitted_at=cfg.submitted_at,
        updated_at=cfg.updated_at,
        repo_slug=cfg.repo_slug,
        nodes=_list_nodes(root, job_slug),
    )


def _read_job_config(root: Path, job_slug: str) -> JobConfig | None:
    cfg_path = v1_paths.job_config_path(job_slug, root=root)
    if not cfg_path.is_file():
        return None
    try:
        return JobConfig.model_validate_json(cfg_path.read_text())
    except Exception:
        return None


def _list_nodes(root: Path, job_slug: str) -> list[NodeListEntry]:
    nodes_dir = v1_paths.nodes_dir(job_slug, root=root)
    if not nodes_dir.is_dir():
        return []
    out: list[NodeListEntry] = []
    for nd in sorted(nodes_dir.iterdir()):
        sp = nd / "state.json"
        if not sp.is_file():
            continue
        try:
            nr = NodeRun.model_validate_json(sp.read_text())
        except Exception:
            continue
        out.append(
            NodeListEntry(
                node_id=nr.node_id,
                state=nr.state,
                attempts=nr.attempts,
                last_error=nr.last_error,
                started_at=nr.started_at,
                finished_at=nr.finished_at,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Node projections
# ---------------------------------------------------------------------------


def node_detail(root: Path, job_slug: str, node_id: str) -> NodeDetail | None:
    sp = v1_paths.node_state_path(job_slug, node_id, root=root)
    if not sp.is_file():
        return None
    try:
        nr = NodeRun.model_validate_json(sp.read_text())
    except Exception:
        return None

    outputs: dict[str, dict[str, Any]] = {}
    var_dir = v1_paths.variables_dir(job_slug, root=root)
    if var_dir.is_dir():
        for env_path in sorted(var_dir.glob("*.json")):
            try:
                env = Envelope.model_validate_json(env_path.read_text())
            except Exception:
                continue
            if env.producer_node == node_id:
                outputs[env_path.stem] = json.loads(env.model_dump_json())

    return NodeDetail(
        node_id=nr.node_id,
        state=nr.state,
        attempts=nr.attempts,
        last_error=nr.last_error,
        started_at=nr.started_at,
        finished_at=nr.finished_at,
        outputs=outputs,
    )


# ---------------------------------------------------------------------------
# HIL projections
# ---------------------------------------------------------------------------


def hil_queue(root: Path, *, job_slug: str | None = None) -> list[HilQueueItem]:
    """Enumerate every pending HIL gate, optionally filtered to one job."""
    items: list[HilQueueItem] = []
    if job_slug is not None:
        items.extend(_hil_queue_for_job(root, job_slug))
        return items
    jobs_dir = v1_paths.jobs_dir(root=root)
    if jobs_dir.is_dir():
        for jd in sorted(jobs_dir.iterdir()):
            items.extend(_hil_queue_for_job(root, jd.name))
    return items


def _hil_queue_for_job(root: Path, job_slug: str) -> list[HilQueueItem]:
    pending_dir = v1_paths.job_dir(job_slug, root=root) / "pending"
    if not pending_dir.is_dir():
        return []
    out: list[HilQueueItem] = []
    for f in sorted(pending_dir.glob("*.json")):
        item = _read_pending_marker(root, job_slug, f)
        if item is not None:
            out.append(item)
    return out


def hil_queue_item(root: Path, job_slug: str, node_id: str) -> HilQueueItem | None:
    f = v1_paths.job_dir(job_slug, root=root) / "pending" / f"{node_id}.json"
    if not f.is_file():
        return None
    return _read_pending_marker(root, job_slug, f)


def _read_pending_marker(root: Path, job_slug: str, path: Path) -> HilQueueItem | None:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    created_at: datetime | None = None
    raw_created = data.get("created_at")
    if isinstance(raw_created, str):
        try:
            created_at = datetime.fromisoformat(raw_created)
        except ValueError:
            created_at = None
    return HilQueueItem(
        job_slug=job_slug,
        node_id=data.get("node_id", path.stem),
        output_var_names=list(data.get("output_var_names") or []),
        output_types=dict(data.get("output_types") or {}),
        presentation=dict(data.get("presentation") or {}),
        loop_id=data.get("loop_id"),
        iteration=data.get("iteration"),
        created_at=created_at,
    )
