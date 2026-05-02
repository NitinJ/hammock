"""Typed in-memory cache over the hammock root.

Single source of truth for the dashboard's reads. The :class:`Cache` is
populated from disk at bootstrap and kept in sync by the watcher
(:mod:`dashboard.watcher.tailer`).

Scope of v0 (Stage 1):

- Tracks the four state-file kinds: ``project.json``, ``job.json``,
  ``stage.json``, ``hil/<id>.json``.
- Streams (``events.jsonl``, ``messages.jsonl``, ``tool-uses.jsonl``,
  ``nudges.jsonl``) are NOT cached. They are tailed by Stage 10's SSE
  layer for replay; the on-disk file is always the source of truth.
- Append-only logs are read-on-demand by the cost rollup (Stage 9) and
  archival (Stage 4+). Holding them in memory has no value.

The cache is an in-memory denormalisation of disk state. Restart is free.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from shared.models import HilItem, JobConfig, ProjectConfig, StageRun

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassifiedPath:
    """The decoded meaning of an absolute path inside the hammock root.

    ``kind`` names what file the path represents; identifying ids
    (``project_slug``, ``job_slug``, ``stage_id``, ``hil_id``) are filled
    according to ``kind``.
    """

    kind: Literal["project", "job", "stage", "hil", "unknown"]
    project_slug: str | None = None
    job_slug: str | None = None
    stage_id: str | None = None
    hil_id: str | None = None


def classify_path(path: Path, root: Path) -> ClassifiedPath:
    """Map an absolute *path* under *root* to its semantic kind.

    Returns a :class:`ClassifiedPath` with ``kind="unknown"`` for paths the
    cache does not track (event logs, raw artifacts, side files). The cache
    is silent on unknown paths; the watcher logs at debug level.
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ClassifiedPath("unknown")

    parts = rel.parts

    # projects/<slug>/project.json
    if len(parts) == 3 and parts[0] == "projects" and parts[2] == "project.json":
        return ClassifiedPath("project", project_slug=parts[1])

    # jobs/<slug>/job.json
    if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "job.json":
        return ClassifiedPath("job", job_slug=parts[1])

    # jobs/<slug>/stages/<sid>/stage.json
    if len(parts) == 5 and parts[0] == "jobs" and parts[2] == "stages" and parts[4] == "stage.json":
        return ClassifiedPath("stage", job_slug=parts[1], stage_id=parts[3])

    # jobs/<slug>/hil/<id>.json
    if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "hil" and parts[3].endswith(".json"):
        return ClassifiedPath(
            "hil",
            job_slug=parts[1],
            hil_id=parts[3][: -len(".json")],
        )

    return ClassifiedPath("unknown")


# ---------------------------------------------------------------------------
# Change kinds
# ---------------------------------------------------------------------------


class ChangeKind(StrEnum):
    """The mutation a watcher event represents.

    Mapped from :class:`watchfiles.Change` by :mod:`dashboard.watcher.tailer`.
    """

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


class Cache:
    """In-memory typed cache over the hammock root.

    Construct via :meth:`bootstrap`, never directly. Bootstrap walks the
    root once; subsequent updates flow through :meth:`apply_change`.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        # Keyed by the identifying tuple (typically the slug or id).
        self._projects: dict[str, ProjectConfig] = {}
        self._jobs: dict[str, JobConfig] = {}
        # Stages are scoped per job: (job_slug, stage_id) -> StageRun
        self._stages: dict[tuple[str, str], StageRun] = {}
        # HIL items are globally addressable by item_id (per design doc — id
        # is unique across jobs). Track origin job for filtering.
        self._hil: dict[str, HilItem] = {}
        self._hil_job: dict[str, str] = {}

    # -- bootstrap ----------------------------------------------------------

    @classmethod
    async def bootstrap(cls, root: Path) -> Cache:
        """Construct a fresh cache by reading every tracked file under *root*.

        Async because the public API in subsequent stages is async; the
        Stage-1 implementation is synchronous I/O behind it. Bootstrap is
        a one-shot operation, so blocking briefly during startup is fine.
        """
        cache = cls(root)
        if not root.exists():
            return cache
        cache._scan(root)
        return cache

    def _scan(self, root: Path) -> None:
        """Walk *root* once, applying every state file to the cache."""
        for path in root.rglob("*.json"):
            if not path.is_file():
                continue
            cls = classify_path(path, root)
            if cls.kind == "unknown":
                continue
            self._load_into(path, cls)

    def _load_into(self, path: Path, cls: ClassifiedPath) -> None:
        try:
            content = path.read_text()
        except OSError as e:
            LOG.warning("cache: cannot read %s: %s", path, e)
            return
        try:
            self._parse_and_store(content, cls)
        except Exception as e:  # broad: malformed JSON, validation errors
            LOG.warning("cache: cannot parse %s: %s", path, e)

    def _parse_and_store(self, content: str, cls: ClassifiedPath) -> None:
        if cls.kind == "project":
            assert cls.project_slug is not None
            self._projects[cls.project_slug] = ProjectConfig.model_validate_json(content)
        elif cls.kind == "job":
            assert cls.job_slug is not None
            self._jobs[cls.job_slug] = JobConfig.model_validate_json(content)
        elif cls.kind == "stage":
            assert cls.job_slug is not None and cls.stage_id is not None
            self._stages[(cls.job_slug, cls.stage_id)] = StageRun.model_validate_json(content)
        elif cls.kind == "hil":
            assert cls.job_slug is not None and cls.hil_id is not None
            self._hil[cls.hil_id] = HilItem.model_validate_json(content)
            self._hil_job[cls.hil_id] = cls.job_slug

    # -- read API -----------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    def get_project(self, slug: str) -> ProjectConfig | None:
        return self._projects.get(slug)

    def list_projects(self) -> list[ProjectConfig]:
        return list(self._projects.values())

    def get_job(self, job_slug: str) -> JobConfig | None:
        return self._jobs.get(job_slug)

    def list_jobs(self, project_slug: str | None = None) -> list[JobConfig]:
        if project_slug is None:
            return list(self._jobs.values())
        return [j for j in self._jobs.values() if j.project_slug == project_slug]

    def get_stage(self, job_slug: str, stage_id: str) -> StageRun | None:
        return self._stages.get((job_slug, stage_id))

    def list_stages(self, job_slug: str) -> list[StageRun]:
        return [s for (j, _), s in self._stages.items() if j == job_slug]

    def get_hil(self, item_id: str) -> HilItem | None:
        return self._hil.get(item_id)

    def hil_job_slug(self, item_id: str) -> str | None:
        """Return the job slug that owns *item_id*, or ``None`` if unknown.

        HIL items are addressable by id alone (per design doc), but the
        owning job is needed for routing, projections, and SSE scoping.
        """
        return self._hil_job.get(item_id)

    def list_hil(
        self,
        *,
        job_slug: str | None = None,
        status: Literal["awaiting", "answered", "cancelled"] | None = None,
    ) -> list[HilItem]:
        result = list(self._hil.values())
        if job_slug is not None:
            result = [h for h in result if self._hil_job.get(h.id) == job_slug]
        if status is not None:
            result = [h for h in result if h.status == status]
        return result

    # -- write API (called by the watcher) ----------------------------------

    def apply_change(self, path: Path, kind: ChangeKind) -> ClassifiedPath:
        """Reflect a filesystem change in the cache.

        Returns the :class:`ClassifiedPath` for the changed path so the
        watcher can derive a pub/sub scope without re-classifying.
        """
        cls = classify_path(path, self._root)
        if cls.kind == "unknown":
            return cls

        if kind is ChangeKind.DELETED:
            self._remove(cls)
            return cls

        # ADDED or MODIFIED: re-read and validate. Errors are logged and
        # swallowed; the cache stays at its last good value rather than
        # crashing on bad disk state.
        self._load_into(path, cls)
        return cls

    def _remove(self, cls: ClassifiedPath) -> None:
        if cls.kind == "project" and cls.project_slug is not None:
            self._projects.pop(cls.project_slug, None)
        elif cls.kind == "job" and cls.job_slug is not None:
            self._jobs.pop(cls.job_slug, None)
        elif cls.kind == "stage" and cls.job_slug is not None and cls.stage_id is not None:
            self._stages.pop((cls.job_slug, cls.stage_id), None)
        elif cls.kind == "hil" and cls.hil_id is not None:
            self._hil.pop(cls.hil_id, None)
            self._hil_job.pop(cls.hil_id, None)

    # -- diagnostics --------------------------------------------------------

    def size(self) -> dict[str, int]:
        """Counts of each cached entity — used by ``/api/health`` later."""
        return {
            "projects": len(self._projects),
            "jobs": len(self._jobs),
            "stages": len(self._stages),
            "hil": len(self._hil),
        }
