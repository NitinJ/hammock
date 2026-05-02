"""Cache → view projections.

Per design doc § Cache → view projections. Projections are pure functions
over :class:`Cache` state (plus on-demand reads of append-only event logs
for cost rollups). They return the slim view shapes the HTTP layer
serialises directly to JSON.

Conventions
-----------
- Each projection is a free function taking the :class:`Cache` (and any
  additional ids) as arguments. No I/O against ``~/.hammock/`` happens
  here other than the cost-rollup fold over ``events.jsonl`` files —
  those are explicitly read-on-demand (Stage 1 deliberately does not
  cache append-only logs).
- ``None`` is the canonical "not found" return; HTTP routes translate it
  into a 404.
- Time-relative fields (``age_seconds``) take ``now`` as a parameter so
  tests can pin time deterministically.
- Models live in this module — they are response shapes the API layer
  exposes verbatim.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dashboard.state.cache import Cache
from shared import paths
from shared.models import (
    HilItem,
    JobConfig,
    JobState,
    ProjectConfig,
    StageRun,
    StageState,
    TaskRecord,
)

DoctorStatus = Literal["pass", "warn", "fail", "unknown"]


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class ProjectListItem(BaseModel):
    """Slim project summary for the project list and home recent-projects."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    repo_path: str
    default_branch: str
    total_jobs: int = Field(ge=0)
    open_hil_count: int = Field(ge=0)
    last_job_at: datetime | None = None
    doctor_status: DoctorStatus = "unknown"


class ProjectDetail(BaseModel):
    """Full project view — registry record plus job/HIL counts."""

    model_config = ConfigDict(extra="forbid")

    project: ProjectConfig
    total_jobs: int = Field(ge=0)
    open_hil_count: int = Field(ge=0)
    jobs_by_state: dict[str, int] = Field(default_factory=dict)


class JobListItem(BaseModel):
    """Slim job row for project-detail and home recent-jobs."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_slug: str
    project_slug: str
    job_type: str
    state: JobState
    created_at: datetime
    total_cost_usd: float = Field(ge=0)
    current_stage_id: str | None = None


class StageListEntry(BaseModel):
    """One row in the stage timeline of a job-detail view."""

    model_config = ConfigDict(extra="forbid")

    stage_id: str
    state: StageState
    attempt: int = Field(ge=1)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    cost_accrued: float = Field(ge=0)


class JobDetail(BaseModel):
    """Job-overview projection — job + ordered stages + cost rollup."""

    model_config = ConfigDict(extra="forbid")

    job: JobConfig
    stages: list[StageListEntry] = Field(default_factory=list)
    total_cost_usd: float = Field(ge=0)


class StageDetail(BaseModel):
    """Stage-live-view projection — stage + tasks (read on demand from disk)."""

    model_config = ConfigDict(extra="forbid")

    job_slug: str
    stage: StageRun
    tasks: list[TaskRecord] = Field(default_factory=list)


class ActiveStageStripItem(BaseModel):
    """Card data for the home active-stages strip."""

    model_config = ConfigDict(extra="forbid")

    project_slug: str
    job_slug: str
    stage_id: str
    state: StageState
    cost_accrued: float = Field(ge=0)
    started_at: datetime | None = None


class HilQueueItem(BaseModel):
    """One row in the HIL queue."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    kind: Literal["ask", "review", "manual-step"]
    status: Literal["awaiting", "answered", "cancelled"]
    stage_id: str
    job_slug: str
    project_slug: str | None = None
    created_at: datetime
    age_seconds: float = Field(ge=0)


class CostRollup(BaseModel):
    """Cost rollup for a scope — folded from ``cost_accrued`` events."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["project", "job", "stage"]
    id: str
    total_usd: float = Field(ge=0)
    total_tokens: int = Field(ge=0)
    by_stage: dict[str, float] = Field(default_factory=dict)
    by_agent: dict[str, float] = Field(default_factory=dict)


class SystemHealth(BaseModel):
    """Top-level system health snapshot for the home and settings views."""

    model_config = ConfigDict(extra="forbid")

    cache_size: dict[str, int] = Field(default_factory=dict)
    watcher_alive: bool = True
    mcp_server_count: int = 0
    drivers_alive: int = 0


class ObservatoryMetrics(BaseModel):
    """v0 stub for ``/api/observatory/metrics``.

    Soul / Council land in v2+; this view ships an empty payload now so the
    route exists for consumers (the home health strip references it).
    """

    model_config = ConfigDict(extra="forbid")

    sampled_events: int = 0
    proposals_emitted: int = 0
    reviewer_verdicts: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _job_total_cost(cache: Cache, job_slug: str) -> float:
    """Sum ``cost_accrued`` over a job — events.jsonl is the source of truth.

    Stage runs in cache also carry a ``cost_accrued`` field; the events log
    is authoritative when present, and we fall back to the stages otherwise
    so a job mid-flight (no events written yet) still reports a number.
    """
    events_path = paths.job_events_jsonl(job_slug, root=cache.root)
    total, _ = _fold_cost_events(events_path)
    if total > 0:
        return total
    return sum(s.cost_accrued for s in cache.list_stages(job_slug))


def _fold_cost_events(events_jsonl: Path) -> tuple[float, int]:
    """Return ``(total_usd, total_tokens)`` from ``cost_accrued`` events.

    Reads the file line-by-line, ignoring malformed lines (they log a
    warning at the cache level; here we just skip). The payload convention
    for ``cost_accrued`` events is ``{"usd": <float>, "tokens": <int>}``.
    """
    if not events_jsonl.is_file():
        return 0.0, 0
    total_usd = 0.0
    total_tokens = 0
    with events_jsonl.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event_type") != "cost_accrued":
                continue
            payload = obj.get("payload") or {}
            usd = payload.get("usd")
            if isinstance(usd, int | float):
                total_usd += float(usd)
            tokens = payload.get("tokens")
            if isinstance(tokens, int):
                total_tokens += tokens
    return total_usd, total_tokens


def _fold_cost_breakdown(
    events_jsonl: Path,
) -> tuple[float, int, dict[str, float], dict[str, float]]:
    """Like :func:`_fold_cost_events` but also breaks down by stage and agent."""
    if not events_jsonl.is_file():
        return 0.0, 0, {}, {}
    total_usd = 0.0
    total_tokens = 0
    by_stage: dict[str, float] = {}
    by_agent: dict[str, float] = {}
    with events_jsonl.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("event_type") != "cost_accrued":
                continue
            payload = obj.get("payload") or {}
            usd_raw = payload.get("usd")
            if not isinstance(usd_raw, int | float):
                continue
            usd = float(usd_raw)
            total_usd += usd
            tokens = payload.get("tokens")
            if isinstance(tokens, int):
                total_tokens += tokens
            stage_id = obj.get("stage_id")
            if isinstance(stage_id, str):
                by_stage[stage_id] = by_stage.get(stage_id, 0.0) + usd
            agent_ref = payload.get("agent_ref")
            if isinstance(agent_ref, str):
                by_agent[agent_ref] = by_agent.get(agent_ref, 0.0) + usd
    return total_usd, total_tokens, by_stage, by_agent


def _doctor_status(project: ProjectConfig) -> DoctorStatus:
    s = project.last_health_check_status
    if s is None:
        return "unknown"
    return s


def _open_hil_for_project(cache: Cache, project_slug: str) -> int:
    job_slugs = {j.job_slug for j in cache.list_jobs(project_slug)}
    return sum(
        1 for h in cache.list_hil(status="awaiting") if cache.hil_job_slug(h.id) in job_slugs
    )


def _last_job_at(cache: Cache, project_slug: str) -> datetime | None:
    jobs = cache.list_jobs(project_slug)
    if not jobs:
        return None
    return max(j.created_at for j in jobs)


def _current_stage_id(cache: Cache, job_slug: str) -> str | None:
    """Best-effort current-stage hint: the latest non-terminal stage."""
    stages = cache.list_stages(job_slug)
    if not stages:
        return None
    running_states = {
        StageState.RUNNING,
        StageState.READY,
        StageState.PENDING,
        StageState.WRAPPING_UP,
        StageState.PARTIALLY_BLOCKED,
        StageState.BLOCKED_ON_HUMAN,
        StageState.ATTENTION_NEEDED,
    }
    for s in stages:
        if s.state in running_states:
            return s.stage_id
    # Otherwise, the most recently started one.
    started = [s for s in stages if s.started_at is not None]
    if started:
        latest = max(started, key=lambda s: s.started_at or datetime.min.replace(tzinfo=UTC))
        return latest.stage_id
    return stages[0].stage_id


def _project_slug_for_job(cache: Cache, job_slug: str) -> str | None:
    job = cache.get_job(job_slug)
    return job.project_slug if job is not None else None


# ---------------------------------------------------------------------------
# Project projections
# ---------------------------------------------------------------------------


def project_list_item(cache: Cache, slug: str) -> ProjectListItem | None:
    project = cache.get_project(slug)
    if project is None:
        return None
    jobs = cache.list_jobs(slug)
    return ProjectListItem(
        slug=project.slug,
        name=project.name,
        repo_path=project.repo_path,
        default_branch=project.default_branch,
        total_jobs=len(jobs),
        open_hil_count=_open_hil_for_project(cache, slug),
        last_job_at=_last_job_at(cache, slug),
        doctor_status=_doctor_status(project),
    )


def project_list(cache: Cache) -> list[ProjectListItem]:
    out: list[ProjectListItem] = []
    for p in cache.list_projects():
        item = project_list_item(cache, p.slug)
        if item is not None:
            out.append(item)
    out.sort(key=lambda i: i.slug)
    return out


def project_detail(cache: Cache, slug: str) -> ProjectDetail | None:
    project = cache.get_project(slug)
    if project is None:
        return None
    jobs = cache.list_jobs(slug)
    by_state: dict[str, int] = {}
    for j in jobs:
        by_state[j.state.value] = by_state.get(j.state.value, 0) + 1
    return ProjectDetail(
        project=project,
        total_jobs=len(jobs),
        open_hil_count=_open_hil_for_project(cache, slug),
        jobs_by_state=by_state,
    )


# ---------------------------------------------------------------------------
# Job projections
# ---------------------------------------------------------------------------


def job_list_item(cache: Cache, job_slug: str) -> JobListItem | None:
    job = cache.get_job(job_slug)
    if job is None:
        return None
    return JobListItem(
        job_id=job.job_id,
        job_slug=job.job_slug,
        project_slug=job.project_slug,
        job_type=job.job_type,
        state=job.state,
        created_at=job.created_at,
        total_cost_usd=_job_total_cost(cache, job.job_slug),
        current_stage_id=_current_stage_id(cache, job.job_slug),
    )


def job_list(
    cache: Cache,
    *,
    project_slug: str | None = None,
    status: JobState | None = None,
) -> list[JobListItem]:
    jobs: list[JobConfig] = cache.list_jobs(project_slug)
    if status is not None:
        jobs = [j for j in jobs if j.state == status]
    out: list[JobListItem] = []
    for j in jobs:
        item = job_list_item(cache, j.job_slug)
        if item is not None:
            out.append(item)
    out.sort(key=lambda i: i.created_at, reverse=True)
    return out


def _stage_sort_key(s: StageRun) -> tuple[datetime, str]:
    started = s.started_at or datetime.min.replace(tzinfo=UTC)
    return (started, s.stage_id)


def job_detail(cache: Cache, job_slug: str) -> JobDetail | None:
    job = cache.get_job(job_slug)
    if job is None:
        return None
    stages = sorted(cache.list_stages(job_slug), key=_stage_sort_key)
    entries = [
        StageListEntry(
            stage_id=s.stage_id,
            state=s.state,
            attempt=s.attempt,
            started_at=s.started_at,
            ended_at=s.ended_at,
            cost_accrued=s.cost_accrued,
        )
        for s in stages
    ]
    return JobDetail(
        job=job,
        stages=entries,
        total_cost_usd=_job_total_cost(cache, job_slug),
    )


# ---------------------------------------------------------------------------
# Stage projections
# ---------------------------------------------------------------------------


def _read_tasks(cache: Cache, job_slug: str, stage_id: str) -> list[TaskRecord]:
    tasks_root = paths.tasks_dir(job_slug, stage_id, root=cache.root)
    if not tasks_root.is_dir():
        return []
    out: list[TaskRecord] = []
    for task_dir in sorted(tasks_root.iterdir()):
        if not task_dir.is_dir():
            continue
        task_json = task_dir / "task.json"
        if not task_json.is_file():
            continue
        try:
            out.append(TaskRecord.model_validate_json(task_json.read_text()))
        except (ValueError, OSError):
            continue
    return out


def stage_detail(cache: Cache, job_slug: str, stage_id: str) -> StageDetail | None:
    stage = cache.get_stage(job_slug, stage_id)
    if stage is None:
        return None
    return StageDetail(
        job_slug=job_slug,
        stage=stage,
        tasks=_read_tasks(cache, job_slug, stage_id),
    )


def active_stage_strip(cache: Cache) -> list[ActiveStageStripItem]:
    out: list[ActiveStageStripItem] = []
    active = {StageState.RUNNING, StageState.ATTENTION_NEEDED}
    for job in cache.list_jobs():
        for stage in cache.list_stages(job.job_slug):
            if stage.state not in active:
                continue
            out.append(
                ActiveStageStripItem(
                    project_slug=job.project_slug,
                    job_slug=job.job_slug,
                    stage_id=stage.stage_id,
                    state=stage.state,
                    cost_accrued=stage.cost_accrued,
                    started_at=stage.started_at,
                )
            )
    out.sort(key=lambda i: i.started_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out


# ---------------------------------------------------------------------------
# HIL projections
# ---------------------------------------------------------------------------


def hil_queue_item(
    cache: Cache,
    item_id: str,
    *,
    now: datetime | None = None,
) -> HilQueueItem | None:
    item = cache.get_hil(item_id)
    if item is None:
        return None
    job_slug = cache.hil_job_slug(item_id)
    if job_slug is None:
        return None
    project_slug = _project_slug_for_job(cache, job_slug)
    n = now if now is not None else _now()
    age = (n - item.created_at).total_seconds()
    return HilQueueItem(
        item_id=item.id,
        kind=item.kind,
        status=item.status,
        stage_id=item.stage_id,
        job_slug=job_slug,
        project_slug=project_slug,
        created_at=item.created_at,
        age_seconds=max(age, 0.0),
    )


def hil_queue(
    cache: Cache,
    *,
    status: Literal["awaiting", "answered", "cancelled"] | None = "awaiting",
    kind: Literal["ask", "review", "manual-step"] | None = None,
    project_slug: str | None = None,
    job_slug: str | None = None,
    now: datetime | None = None,
) -> list[HilQueueItem]:
    items: list[HilItem] = cache.list_hil(status=status, job_slug=job_slug)
    if kind is not None:
        items = [h for h in items if h.kind == kind]
    out: list[HilQueueItem] = []
    for h in items:
        row = hil_queue_item(cache, h.id, now=now)
        if row is None:
            continue
        if project_slug is not None and row.project_slug != project_slug:
            continue
        out.append(row)
    out.sort(key=lambda i: i.created_at)  # oldest first per design doc
    return out


# ---------------------------------------------------------------------------
# Cost rollup
# ---------------------------------------------------------------------------


def cost_rollup(
    cache: Cache,
    scope: Literal["project", "job", "stage"],
    id_: str,
    *,
    stage_job_slug: str | None = None,
) -> CostRollup | None:
    """Cost rollup for a scope.

    - ``project``: id_ is the project slug; folds events from every job in
      that project.
    - ``job``: id_ is the job slug; folds the job-level events.jsonl.
    - ``stage``: id_ is the stage_id; ``stage_job_slug`` must be supplied to
      pick the right stage events.jsonl.

    Returns ``None`` for a non-existent project/job/stage.
    """
    if scope == "project":
        if cache.get_project(id_) is None:
            return None
        total_usd = 0.0
        total_tokens = 0
        by_stage: dict[str, float] = {}
        by_agent: dict[str, float] = {}
        for job in cache.list_jobs(id_):
            ej = paths.job_events_jsonl(job.job_slug, root=cache.root)
            u, t, bs, ba = _fold_cost_breakdown(ej)
            total_usd += u
            total_tokens += t
            for k, v in bs.items():
                by_stage[k] = by_stage.get(k, 0.0) + v
            for k, v in ba.items():
                by_agent[k] = by_agent.get(k, 0.0) + v
        return CostRollup(
            scope=scope,
            id=id_,
            total_usd=total_usd,
            total_tokens=total_tokens,
            by_stage=by_stage,
            by_agent=by_agent,
        )

    if scope == "job":
        if cache.get_job(id_) is None:
            return None
        ej = paths.job_events_jsonl(id_, root=cache.root)
        u, t, bs, ba = _fold_cost_breakdown(ej)
        return CostRollup(
            scope=scope,
            id=id_,
            total_usd=u,
            total_tokens=t,
            by_stage=bs,
            by_agent=ba,
        )

    if scope == "stage":
        if stage_job_slug is None:
            return None
        if cache.get_stage(stage_job_slug, id_) is None:
            return None
        ej = paths.stage_events_jsonl(stage_job_slug, id_, root=cache.root)
        u, t, _bs, ba = _fold_cost_breakdown(ej)
        return CostRollup(
            scope=scope,
            id=id_,
            total_usd=u,
            total_tokens=t,
            by_stage={id_: u} if u > 0 else {},
            by_agent=ba,
        )


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------


def system_health(cache: Cache, *, watcher_alive: bool = True) -> SystemHealth:
    return SystemHealth(
        cache_size=cache.size(),
        watcher_alive=watcher_alive,
        mcp_server_count=0,
        drivers_alive=0,
    )
