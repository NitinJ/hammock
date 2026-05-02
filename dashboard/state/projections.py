"""Cache → view projections.

Per design doc § Cache → view projections. Projections are pure functions
over :class:`Cache` state (plus on-demand reads of append-only event logs
for cost rollups). They return the slim view shapes the HTTP layer
serialises directly to JSON.

Stage-9 skeleton: types defined, function bodies raise
``NotImplementedError``. Implementation lands in the follow-up commit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from dashboard.state.cache import Cache
from shared.models import (
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
    model_config = ConfigDict(extra="forbid")

    project: ProjectConfig
    total_jobs: int = Field(ge=0)
    open_hil_count: int = Field(ge=0)
    jobs_by_state: dict[str, int] = Field(default_factory=dict)


class JobListItem(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

    stage_id: str
    state: StageState
    attempt: int = Field(ge=1)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    cost_accrued: float = Field(ge=0)


class JobDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: JobConfig
    stages: list[StageListEntry] = Field(default_factory=list)
    total_cost_usd: float = Field(ge=0)


class StageDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_slug: str
    stage: StageRun
    tasks: list[TaskRecord] = Field(default_factory=list)


class ActiveStageStripItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_slug: str
    job_slug: str
    stage_id: str
    state: StageState
    cost_accrued: float = Field(ge=0)
    started_at: datetime | None = None


class HilQueueItem(BaseModel):
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
    model_config = ConfigDict(extra="forbid")

    scope: Literal["project", "job", "stage"]
    id: str
    total_usd: float = Field(ge=0)
    total_tokens: int = Field(ge=0)
    by_stage: dict[str, float] = Field(default_factory=dict)
    by_agent: dict[str, float] = Field(default_factory=dict)


class SystemHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cache_size: dict[str, int] = Field(default_factory=dict)
    watcher_alive: bool = True
    mcp_server_count: int = 0
    drivers_alive: int = 0


class ObservatoryMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sampled_events: int = 0
    proposals_emitted: int = 0
    reviewer_verdicts: int = 0


# ---------------------------------------------------------------------------
# Projection function signatures (impl lands in the follow-up commit)
# ---------------------------------------------------------------------------


def project_list_item(cache: Cache, slug: str) -> ProjectListItem | None:
    raise NotImplementedError


def project_list(cache: Cache) -> list[ProjectListItem]:
    raise NotImplementedError


def project_detail(cache: Cache, slug: str) -> ProjectDetail | None:
    raise NotImplementedError


def job_list_item(cache: Cache, job_slug: str) -> JobListItem | None:
    raise NotImplementedError


def job_list(
    cache: Cache,
    *,
    project_slug: str | None = None,
    status: JobState | None = None,
) -> list[JobListItem]:
    raise NotImplementedError


def job_detail(cache: Cache, job_slug: str) -> JobDetail | None:
    raise NotImplementedError


def stage_detail(cache: Cache, job_slug: str, stage_id: str) -> StageDetail | None:
    raise NotImplementedError


def active_stage_strip(cache: Cache) -> list[ActiveStageStripItem]:
    raise NotImplementedError


def hil_queue_item(
    cache: Cache, item_id: str, *, now: datetime | None = None
) -> HilQueueItem | None:
    raise NotImplementedError


def hil_queue(
    cache: Cache,
    *,
    status: Literal["awaiting", "answered", "cancelled"] | None = "awaiting",
    kind: Literal["ask", "review", "manual-step"] | None = None,
    project_slug: str | None = None,
    job_slug: str | None = None,
    now: datetime | None = None,
) -> list[HilQueueItem]:
    raise NotImplementedError


def cost_rollup(
    cache: Cache,
    scope: Literal["project", "job", "stage"],
    id_: str,
    *,
    stage_job_slug: str | None = None,
) -> CostRollup | None:
    raise NotImplementedError


def system_health(cache: Cache, *, watcher_alive: bool = True) -> SystemHealth:
    raise NotImplementedError
