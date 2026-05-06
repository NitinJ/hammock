"""FakeEngine — disk-side scripting helper for v1 integration tests.

Stage 1, Step 0 stub. No implementation yet — every behavioural method
raises NotImplementedError. Step 1 tests call against this surface; Step 2
implements method-by-method.

Design — see docs/hammock-impl-patch.md §1.4.

The class writes files via shared.atomic.* — same primitives the real
engine uses — so resulting disk state is byte-identical to a real run.
No driver process is spawned.

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

from pathlib import Path
from typing import Any

from pydantic import BaseModel

from shared.models.job import JobState


class FakeEngine:
    """Scripts v1 disk state for a single job.

    Construction: ``FakeEngine(root, job_slug)``. The constructor does
    NOT create any files; call ``start_job`` to lay down the skeleton.

    Iteration coordinates: ``iter`` is a tuple matching the design's
    iteration descriptor. ``()`` for top-level nodes, ``(n,)`` for
    nodes inside one loop, ``(outer, inner, ...)`` for nested loops.
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

    # ------------------------------------------------------------------
    # Job lifecycle
    # ------------------------------------------------------------------

    def start_job(self, *, workflow: dict[str, Any], request: str) -> None:
        """Create the job directory skeleton, write job.json (state=SUBMITTED),
        write workflow.json verbatim, append a job_submitted event."""
        raise NotImplementedError

    def finish_job(self, state: JobState) -> None:
        """Update job.json's ``state`` field and append a terminal-state
        event. ``state`` must be a terminal state (COMPLETED / ABANDONED
        / FAILED)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Node lifecycle
    # ------------------------------------------------------------------

    def enter_node(self, node_id: str, *, iter: tuple[int, ...] = ()) -> None:
        """Write nodes/<id>/state.json with state=running and increment
        attempts. Append a node_started event."""
        raise NotImplementedError

    def complete_node(
        self,
        node_id: str,
        value: BaseModel,
        *,
        iter: tuple[int, ...] = (),
    ) -> None:
        """Write the variable envelope to variables/ (loop-indexed when
        ``iter`` is non-empty), update state.json to state=succeeded,
        append a node_succeeded event."""
        raise NotImplementedError

    def fail_node(
        self,
        node_id: str,
        error: str,
        *,
        iter: tuple[int, ...] = (),
    ) -> None:
        """Update state.json to state=failed with last_error=error.
        Append a node_failed event."""
        raise NotImplementedError

    def skip_node(
        self,
        node_id: str,
        reason: str,
        *,
        iter: tuple[int, ...] = (),
    ) -> None:
        """Update state.json to state=skipped. Append a node_skipped
        event with the reason in payload."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Stream side
    # ------------------------------------------------------------------

    def emit_log(
        self,
        node_id: str,
        line: str,
        *,
        iter: tuple[int, ...] = (),
    ) -> None:
        """Append a single line to the per-attempt stdout.log under
        nodes/<id>/runs/<n>/."""
        raise NotImplementedError

    def emit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        node_id: str | None = None,
        iter: tuple[int, ...] = (),
    ) -> None:
        """Append a typed Event to events.jsonl with the next per-job
        ``seq``. ``node_id`` and iteration coordinates flow into the
        event's stage_id / payload as appropriate."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # HIL
    # ------------------------------------------------------------------

    def request_hil(
        self,
        node_id: str,
        type_name: str,
        *,
        iter: tuple[int, ...] = (),
        prompt: str | None = None,
        output_var_names: list[str] | None = None,
    ) -> str:
        """Drop a pending marker at pending/<node_id>.json (or the
        loop-indexed equivalent). Returns the node_id that the dashboard
        and stitcher use as the gate identifier.

        ``output_var_names`` defaults to ``[node_id]`` if not supplied."""
        raise NotImplementedError

    def assert_hil_answered(self, node_id: str, *, iter: tuple[int, ...] = ()) -> BaseModel:
        """Verify the HIL gate at ``node_id`` has been answered:

        - The pending marker no longer exists.
        - The variable envelope (loop-indexed when ``iter`` is non-empty)
          exists and parses against the declared type's Value model.

        Returns the parsed Value. Raises ``AssertionError`` if either
        precondition fails."""
        raise NotImplementedError
