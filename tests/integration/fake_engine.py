"""FakeEngine — disk-side scripting helper for v1 integration tests.

Stage 1 deliverable. Writes the v1 disk layout via shared.atomic.* —
same primitives the real engine uses — so resulting disk state is
byte-identical to a real run. No driver process is spawned.

Design — see docs/hammock-impl-patch.md §1.4.

v1 disk layout (single source of truth: shared.v1.paths):

    jobs/<slug>/job.json
                  events.jsonl
                  variables/<var>.json
                  variables/loop_<id>_<var>_<i>.json
                  nodes/<id>/state.json
                  nodes/<id>/runs/<n>/...
                  pending/<node_id>.json     (HIL pending markers)

There is NO separate hil/answered/ directory: a HIL gate is "answered"
when its pending marker is gone AND the corresponding variable envelope
exists in variables/.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from shared.atomic import atomic_write_text
from shared.models.events import Event
from shared.v1 import paths as v1_paths
from shared.v1.envelope import make_envelope
from shared.v1.job import (
    JobConfig,
    JobState,
    NodeRun,
    NodeRunState,
    make_job_config,
    make_node_run,
)

_TERMINAL_STATES: set[JobState] = {
    JobState.COMPLETED,
    JobState.FAILED,
    JobState.CANCELLED,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    """Append a single JSON line to a JSONL file. Engine uses
    shared.atomic.append; for tests, plain append is sufficient."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def _write_json_atomic(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, model.model_dump_json(indent=2))


def _resolve_envelope_path(
    *,
    job_slug: str,
    var_name: str,
    iter: tuple[int, ...],
    loop_id: str | None,
    root: Path,
) -> Path:
    """Return the path the variable envelope should land at.

    Top-level (iter=()): variables/<var>.json
    Loop-indexed:        variables/loop_<loop_id>_<var>_<i>.json — only
                          single-level supported in Stage 1; nested loop
                          coordinates raise.
    """
    if not iter:
        return v1_paths.variable_envelope_path(job_slug, var_name, root=root)
    if loop_id is None:
        raise ValueError("iter is non-empty but loop_id was not provided")
    if len(iter) > 1:
        raise NotImplementedError(
            "FakeEngine v1 supports single-level loop iteration only; "
            "nested coordinates require multi-level loop_id encoding."
        )
    return v1_paths.loop_variable_envelope_path(
        job_slug, loop_id, var_name, iter[0], root=root
    )


class FakeEngine:
    """Scripts v1 disk state for a single job.

    Construction does NOT touch the filesystem; call ``start_job`` to
    lay down the skeleton.

    Iteration coordinates: ``iter`` is a tuple matching the design's
    iteration descriptor. ``()`` for top-level nodes, ``(n,)`` for
    nodes inside one loop. Pair ``iter`` with ``loop_id`` when
    non-empty so envelope paths can be computed.
    """

    def __init__(
        self,
        root: Path,
        job_slug: str,
        *,
        project_slug: str = "test-project",
    ) -> None:
        self.root = root
        self.job_slug = job_slug
        self.project_slug = project_slug
        # Per-job monotonic event seq. Reset on construction so tests get
        # deterministic seq=0, 1, 2... across the lifetime of the engine.
        self._event_seq: int = 0
        # Track started attempts per node_id for log path construction.
        self._node_attempts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_seq(self) -> int:
        n = self._event_seq
        self._event_seq += 1
        return n

    def _job_dir(self) -> Path:
        return v1_paths.job_dir(self.job_slug, root=self.root)

    def _pending_dir(self) -> Path:
        return self._job_dir() / "pending"

    def _emit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        node_id: str | None = None,
    ) -> None:
        evt = Event(
            seq=self._next_seq(),
            timestamp=_utc_now(),
            event_type=event_type,
            source="job_driver",
            job_id=self.job_slug,
            stage_id=node_id,
            payload=payload,
        )
        _append_jsonl(
            v1_paths.events_jsonl(self.job_slug, root=self.root),
            json.loads(evt.model_dump_json()),
        )

    def _persist_node_run(
        self,
        node_id: str,
        *,
        state: NodeRunState,
        attempts: int,
        last_error: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> None:
        nr = NodeRun(
            node_id=node_id,
            state=state,
            attempts=attempts,
            last_error=last_error,
            started_at=started_at,
            finished_at=finished_at,
        )
        _write_json_atomic(
            v1_paths.node_state_path(self.job_slug, node_id, root=self.root), nr
        )

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    def start_job(self, *, workflow: dict[str, Any], request: str) -> None:
        """Create the job skeleton, write job.json (state=SUBMITTED),
        write workflow.json verbatim, append a job_submitted event."""
        v1_paths.ensure_job_layout(self.job_slug, root=self.root)

        # Workflow snapshot — verbatim copy under the job dir for
        # post-mortem and dashboard introspection.
        workflow_path = self._job_dir() / "workflow.json"
        atomic_write_text(workflow_path, json.dumps(workflow, indent=2))

        # job.json — uses workflow["workflow"] as the workflow_name when
        # present, otherwise the job_slug as a fallback.
        config: JobConfig = make_job_config(
            job_slug=self.job_slug,
            workflow_name=workflow.get("workflow", self.job_slug),
            workflow_path=workflow_path,
            repo_slug=None,
        )
        _write_json_atomic(
            v1_paths.job_config_path(self.job_slug, root=self.root), config
        )

        # Track the request alongside (helpful for the dashboard's
        # job-overview rendering even though it's not part of JobConfig).
        atomic_write_text(self._job_dir() / "request.txt", request)

        self._emit_event(
            "job_submitted",
            {"workflow": config.workflow_name, "request": request},
        )

    def finish_job(self, state: JobState) -> None:
        """Update job.json's ``state`` field and append a terminal-state
        event. ``state`` must be a terminal state."""
        if state not in _TERMINAL_STATES:
            raise ValueError(
                f"finish_job requires a terminal state; got {state.value!r}"
            )

        path = v1_paths.job_config_path(self.job_slug, root=self.root)
        config = JobConfig.model_validate_json(path.read_text())
        updated = config.model_copy(
            update={"state": state, "updated_at": _utc_now()}
        )
        _write_json_atomic(path, updated)

        event_type = {
            JobState.COMPLETED: "job_completed",
            JobState.FAILED: "job_failed",
            JobState.CANCELLED: "job_cancelled",
        }[state]
        self._emit_event(event_type, {"state": state.value})

    # ------------------------------------------------------------------
    # Node lifecycle
    # ------------------------------------------------------------------

    def enter_node(
        self,
        node_id: str,
        *,
        iter: tuple[int, ...] = (),
        loop_id: str | None = None,
    ) -> None:
        """Mark the node as RUNNING; bump attempt count; emit event."""
        attempt = self._node_attempts.get(node_id, 0) + 1
        self._node_attempts[node_id] = attempt
        self._persist_node_run(
            node_id,
            state=NodeRunState.RUNNING,
            attempts=attempt,
            started_at=_utc_now(),
        )
        # Per-attempt artefacts directory is created on first emit_log
        # call to keep enter_node idempotent for state-only assertions.
        self._emit_event(
            "node_started",
            {"node_id": node_id, "attempt": attempt, "iter": list(iter), "loop_id": loop_id},
            node_id=node_id,
        )

    def complete_node(
        self,
        node_id: str,
        value: BaseModel,
        *,
        iter: tuple[int, ...] = (),
        loop_id: str | None = None,
        output_var_name: str | None = None,
        type_name: str | None = None,
    ) -> None:
        """Write the variable envelope to variables/ (loop-indexed when
        ``iter`` is non-empty), update state to SUCCEEDED, emit event.

        ``output_var_name`` defaults to ``node_id``.
        ``type_name`` defaults to the type name derivable from the
        value's class (``BugReportValue`` -> ``bug-report``)."""
        var_name = output_var_name or node_id
        type_name = type_name or _type_name_from_value(value)

        envelope = make_envelope(
            type_name=type_name,
            producer_node=node_id,
            value_payload=json.loads(value.model_dump_json()),
        )
        env_path = _resolve_envelope_path(
            job_slug=self.job_slug,
            var_name=var_name,
            iter=iter,
            loop_id=loop_id,
            root=self.root,
        )
        _write_json_atomic(env_path, envelope)

        attempt = self._node_attempts.get(node_id, 1)
        self._persist_node_run(
            node_id,
            state=NodeRunState.SUCCEEDED,
            attempts=attempt,
            started_at=_utc_now(),
            finished_at=_utc_now(),
        )
        self._emit_event(
            "node_succeeded",
            {
                "node_id": node_id,
                "var_name": var_name,
                "type": type_name,
                "iter": list(iter),
                "loop_id": loop_id,
            },
            node_id=node_id,
        )

    def fail_node(
        self,
        node_id: str,
        error: str,
        *,
        iter: tuple[int, ...] = (),
        loop_id: str | None = None,
    ) -> None:
        """Mark the node FAILED with last_error=error; emit event."""
        attempt = self._node_attempts.get(node_id, 1)
        self._persist_node_run(
            node_id,
            state=NodeRunState.FAILED,
            attempts=attempt,
            last_error=error,
            started_at=_utc_now(),
            finished_at=_utc_now(),
        )
        self._emit_event(
            "node_failed",
            {"node_id": node_id, "error": error, "iter": list(iter), "loop_id": loop_id},
            node_id=node_id,
        )

    def skip_node(
        self,
        node_id: str,
        reason: str,
        *,
        iter: tuple[int, ...] = (),
        loop_id: str | None = None,
    ) -> None:
        """Mark the node SKIPPED; emit event with the reason in payload."""
        attempt = self._node_attempts.get(node_id, 0) + 1
        self._node_attempts[node_id] = attempt
        nr = make_node_run(node_id)
        nr_with_state = nr.model_copy(
            update={"state": NodeRunState.SKIPPED, "attempts": attempt}
        )
        _write_json_atomic(
            v1_paths.node_state_path(self.job_slug, node_id, root=self.root),
            nr_with_state,
        )
        self._emit_event(
            "node_skipped",
            {"node_id": node_id, "reason": reason, "iter": list(iter), "loop_id": loop_id},
            node_id=node_id,
        )

    # ------------------------------------------------------------------
    # Stream side
    # ------------------------------------------------------------------

    def emit_log(
        self,
        node_id: str,
        line: str,
        *,
        iter: tuple[int, ...] = (),
        loop_id: str | None = None,
    ) -> None:
        """Append a single line to the per-attempt stdout.log under
        nodes/<id>/runs/<n>/."""
        attempt = self._node_attempts.get(node_id, 1)
        attempt_dir = v1_paths.node_attempt_dir(
            self.job_slug, node_id, attempt, root=self.root
        )
        attempt_dir.mkdir(parents=True, exist_ok=True)
        stdout = attempt_dir / "stdout.log"
        with stdout.open("a", encoding="utf-8") as f:
            f.write(line)
            if not line.endswith("\n"):
                f.write("\n")

    def emit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        node_id: str | None = None,
        iter: tuple[int, ...] = (),
        loop_id: str | None = None,
    ) -> None:
        """Append a typed Event to events.jsonl with the next per-job
        ``seq``. ``iter`` and ``loop_id`` are added to the payload when
        non-empty."""
        full = dict(payload)
        if iter:
            full.setdefault("iter", list(iter))
        if loop_id:
            full.setdefault("loop_id", loop_id)
        self._emit_event(event_type, full, node_id=node_id)

    # ------------------------------------------------------------------
    # HIL
    # ------------------------------------------------------------------

    def request_hil(
        self,
        node_id: str,
        type_name: str,
        *,
        iter: tuple[int, ...] = (),
        loop_id: str | None = None,
        prompt: str | None = None,
        output_var_names: list[str] | None = None,
    ) -> str:
        """Drop a pending marker at pending/<node_id>.json. Returns
        node_id (the gate identifier).

        ``output_var_names`` defaults to ``[node_id]``."""
        var_names = output_var_names if output_var_names is not None else [node_id]
        marker = {
            "node_id": node_id,
            "output_var_names": var_names,
            "output_types": dict.fromkeys(var_names, type_name),
            "presentation": {"prompt": prompt} if prompt else {},
            "loop_id": loop_id,
            "iteration": iter[0] if iter else None,
            "created_at": _utc_now().isoformat(),
        }
        path = self._pending_dir() / f"{node_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(marker, indent=2))
        self._emit_event(
            "hil_requested",
            {"node_id": node_id, "type": type_name, "iter": list(iter), "loop_id": loop_id},
            node_id=node_id,
        )
        return node_id

    def assert_hil_answered(
        self,
        node_id: str,
        *,
        iter: tuple[int, ...] = (),
        loop_id: str | None = None,
        output_var_name: str | None = None,
        type_name: str | None = None,
    ) -> BaseModel:
        """Verify the HIL gate has been answered:

        - The pending marker no longer exists.
        - The corresponding variable envelope (loop-indexed when iter is
          non-empty) exists.

        Returns the parsed envelope value as a BaseModel. The exact
        Value class is resolved from the type registry via type_name
        (read off the envelope when not provided)."""
        pending_path = self._pending_dir() / f"{node_id}.json"
        if pending_path.exists():
            raise AssertionError(
                f"HIL pending marker still present at {pending_path}"
            )

        var_name = output_var_name or node_id
        env_path = _resolve_envelope_path(
            job_slug=self.job_slug,
            var_name=var_name,
            iter=iter,
            loop_id=loop_id,
            root=self.root,
        )
        if not env_path.exists():
            raise AssertionError(
                f"variable envelope missing for answered HIL gate: {env_path}"
            )

        envelope_data = json.loads(env_path.read_text())
        resolved_type = type_name or envelope_data.get("type")
        if resolved_type is None:
            raise AssertionError("envelope has no 'type' field; cannot resolve Value class")
        from shared.v1.types.registry import get_type

        type_descriptor = get_type(resolved_type)
        value_cls = type_descriptor.Value
        return value_cls.model_validate(envelope_data["value"])


def _type_name_from_value(value: BaseModel) -> str:
    """Map ``BugReportValue`` -> ``bug-report``, ``ReviewVerdictValue``
    -> ``review-verdict``, etc. Convention-based: strip a trailing
    ``Value`` suffix and convert PascalCase to kebab-case."""
    name = type(value).__name__
    if name.endswith("Value"):
        name = name[: -len("Value")]
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            out.append("-")
        out.append(ch.lower())
    return "".join(out)
