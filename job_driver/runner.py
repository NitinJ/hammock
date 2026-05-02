"""Job Driver — deterministic state-machine executor.

Per design doc § Lifecycle — three nested state machines and
implementation.md § Stage 4.

``JobDriver`` is spawned once per active job (as a subprocess via
``job_driver.__main__``). It:

1. Reads the compiled ``stage-list.yaml`` from the job dir.
2. Transitions ``job.json`` from ``SUBMITTED`` to ``STAGES_RUNNING``.
3. Iterates stages in order:
   - Skips stages whose ``runs_if`` predicate evaluates to false.
   - Skips stages whose required outputs already exist on disk (resume).
   - Runs the ``StageRunner`` (FakeStageRunner in Stage 4; RealStageRunner in
     Stage 5).
   - Handles ``loop_back``: if the condition holds and the attempt counter
     is within ``max_iterations``, re-runs the target stage.
4. When all stages are done: ``STAGES_RUNNING`` → ``COMPLETED``.
5. On unrecoverable failure: ``STAGES_RUNNING`` → ``FAILED``.
6. On SIGTERM or command-file cancel: ``STAGES_RUNNING`` → ``ABANDONED``;
   active stage → ``CANCELLED``.
7. Writes a heartbeat file every ``heartbeat_interval`` seconds (default 30).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from shared import paths
from shared.atomic import atomic_append_jsonl, atomic_write_json, atomic_write_text
from shared.models.events import Event
from shared.models.job import JobConfig, JobState
from shared.models.stage import StageDefinition, StageRun, StageState
from shared.predicate import PredicateError, evaluate_predicate

from job_driver.stage_runner import StageResult, StageRunner

log = logging.getLogger(__name__)

HEARTBEAT_INTERVAL: float = 30.0
COMMAND_POLL_INTERVAL: float = 2.0


class JobDriver:
    """Executes a job's stage list deterministically.

    Parameters
    ----------
    job_slug:
        Identifies the job dir under ``~/.hammock/jobs/<slug>/``.
    root:
        Override for the hammock root (``~/.hammock/`` by default).
    stage_runner:
        Stage execution backend. Defaults to ``FakeStageRunner`` if *not*
        supplied and ``HAMMOCK_FAKE_FIXTURES`` env var points at a dir.
    heartbeat_interval:
        Seconds between heartbeat touches (default 30).
    now_fn:
        Injectable clock for tests (defaults to ``datetime.now(UTC)``).
    """

    def __init__(
        self,
        job_slug: str,
        *,
        root: Path | None = None,
        stage_runner: StageRunner | None = None,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.job_slug = job_slug
        self.root = root
        self._stage_runner = stage_runner
        self.heartbeat_interval = heartbeat_interval
        self._now: Callable[[], datetime] = now_fn or (lambda: datetime.now(UTC))
        self._cancel_event = asyncio.Event()
        self._seq = 0

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    async def run(self) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Internal helpers (interface for tests)
    # ------------------------------------------------------------------

    def _job_dir(self) -> Path:
        return paths.job_dir(self.job_slug, root=self.root)

    def _read_job_config(self) -> JobConfig:
        raise NotImplementedError

    def _write_job_state(self, state: JobState) -> None:
        raise NotImplementedError

    def _read_stages(self) -> list[StageDefinition]:
        raise NotImplementedError

    def _stage_already_succeeded(self, stage_def: StageDefinition) -> bool:
        raise NotImplementedError

    def _inputs_ready(self, stage_def: StageDefinition) -> bool:
        raise NotImplementedError

    def _evaluate_runs_if(self, stage_def: StageDefinition) -> bool:
        raise NotImplementedError

    def _check_cancel_command(self) -> bool:
        raise NotImplementedError

    def _emit_event(
        self,
        event_type: str,
        stage_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError

    def _touch_heartbeat(self) -> None:
        raise NotImplementedError
