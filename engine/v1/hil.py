"""Human-in-the-loop (HIL) submission API and pending-marker helpers.

Per design-patch §3:

- Disk is authoritative. Pending markers and submitted variable envelopes
  live on disk. Any cache is a derived view; never gates visibility.
- Submission is synchronous: the engine runs the variable type's
  ``produce`` immediately to verify the submission. If verification
  fails, the submission is rejected and the human retries.
- Driver never exits while a HIL gate is open — it waits in-process for
  the typed value to land on disk.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shared.atomic import atomic_write_text
from shared.v1 import paths
from shared.v1.envelope import make_envelope
from shared.v1.types.protocol import VariableTypeError
from shared.v1.types.registry import get_type
from shared.v1.workflow import ArtifactNode, Workflow

log = logging.getLogger(__name__)


def pending_dir(job_slug: str, *, root: Path) -> Path:
    return paths.pending_dir(job_slug, root=root)


def pending_marker_path(
    job_slug: str,
    node_id: str,
    iter_path: tuple[int, ...] = (),
    *,
    root: Path,
) -> Path:
    return paths.pending_marker_path(job_slug, node_id, iter_path, root=root)


@dataclass(frozen=True)
class PendingHil:
    """One active HIL gate awaiting human input."""

    node_id: str
    output_var_names: list[str]
    """Workflow-level variable names the human must produce. (For T2 this
    is typically a single review-verdict variable; future stages may have
    multi-output gates.)"""

    presentation: dict[str, Any]
    """The node's `presentation:` block from the YAML — title, summary,
    UI hints. Verbatim."""

    output_types: dict[str, str]
    """Variable name → type name (e.g. {"design_spec_review_human": "review-verdict"}).
    Dashboard uses this to pick the form schema per output."""

    iter_path: tuple[int, ...] = ()
    """Full iter_path of the execution this HIL gate belongs to. Empty
    tuple = top-level gate. The submission API uses this to write the
    typed envelope at the correct iter-keyed path."""

    created_at: str | None = None
    """Marker file's ``created_at`` ISO timestamp. Used by the stitcher
    to distinguish successive instances of the same (node_id, iter_path)
    gate when an enclosing outer loop re-enters the inner loop and
    re-creates the marker."""


def write_pending_marker(
    *,
    job_slug: str,
    node: ArtifactNode,
    workflow: Workflow,
    root: Path,
    iter_path: tuple[int, ...] = (),
) -> None:
    """Engine writes one of these when it reaches a human-actor node and
    needs to wait for the human to submit. The dashboard reads from
    disk (no cache) and renders the form.

    ``iter_path`` keys this gate's execution. The marker filename is
    ``<node_id>__<iter_token>.json`` so concurrent loop-body gates
    don't collide.
    """
    pending = pending_dir(job_slug, root=root)
    pending.mkdir(parents=True, exist_ok=True)
    output_types: dict[str, str] = {}
    output_var_names: list[str] = []
    for _output_name, ref in node.outputs.items():
        var_name = ref.lstrip("$").split(".", 1)[0]
        output_var_names.append(var_name)
        if var_name in workflow.variables:
            output_types[var_name] = workflow.variables[var_name].type
    payload: dict[str, Any] = {
        "node_id": node.id,
        "output_var_names": output_var_names,
        "presentation": node.presentation or {},
        "output_types": output_types,
        "iter_path": list(iter_path),
        "created_at": datetime.now(UTC).isoformat(),
    }
    atomic_write_text(
        pending_marker_path(job_slug, node.id, iter_path, root=root),
        json.dumps(payload, indent=2),
    )


def list_pending(job_slug: str, *, root: Path) -> list[PendingHil]:
    """Read every pending marker on disk. Cache-free."""
    pdir = pending_dir(job_slug, root=root)
    if not pdir.is_dir():
        return []
    out: list[PendingHil] = []
    for p in sorted(pdir.glob("*.json")):
        data = json.loads(p.read_text())
        ip_raw = data.get("iter_path") or []
        out.append(
            PendingHil(
                node_id=data["node_id"],
                output_var_names=list(data["output_var_names"]),
                presentation=dict(data.get("presentation", {})),
                output_types=dict(data.get("output_types", {})),
                iter_path=tuple(int(x) for x in ip_raw),
                created_at=data.get("created_at"),
            )
        )
    return out


def remove_pending_marker(
    job_slug: str,
    node_id: str,
    iter_path: tuple[int, ...] = (),
    *,
    root: Path,
) -> None:
    p = pending_marker_path(job_slug, node_id, iter_path, root=root)
    if p.exists():
        p.unlink()


class HilSubmissionError(Exception):
    """Raised by ``submit_hil_answer`` when the submission cannot be
    accepted (no such pending gate, type verification failed, etc.).

    The caller (dashboard / test stitcher) should surface the message to
    the human. The submission has no side effects when this raises —
    nothing was written to disk."""


def _find_node(workflow: Workflow, node_id: str):
    """Locate *node_id* anywhere in the workflow — top-level or nested
    at any depth inside loop bodies. Returns None if not found."""
    from shared.v1.workflow import LoopNode as _LoopNode

    def _scan(nodes) -> object | None:
        for n in nodes:
            if n.id == node_id:
                return n
            if isinstance(n, _LoopNode):
                found = _scan(n.body)
                if found is not None:
                    return found
        return None

    return _scan(workflow.nodes)


def submit_hil_answer(
    *,
    job_slug: str,
    node_id: str,
    var_name: str,
    value_payload: dict[str, Any],
    root: Path,
    workflow: Workflow,
    iter_path: tuple[int, ...] | None = None,
) -> None:
    """Public submission API for HIL gates. Synchronous; runs the type's
    ``produce`` to verify the submission before accepting it.

    ``iter_path`` selects the marker when more than one gate exists
    against the same node (different iterations of an enclosing loop).
    When ``None`` and only one marker matches *node_id*, that marker is
    used; otherwise the caller must disambiguate.

    On success: the typed envelope is written to disk and the pending
    marker for this node is removed once *all* of the node's declared
    outputs have been submitted.
    """
    # 1. Find the node + verify it's human-actor.
    # Walks loop bodies too, since HIL nodes can live inside a loop.
    node = _find_node(workflow, node_id)
    if node is None or not isinstance(node, ArtifactNode):
        raise HilSubmissionError(f"unknown or non-artifact node {node_id!r}")
    if node.actor != "human":
        raise HilSubmissionError(
            f"node {node_id!r} actor is {node.actor!r}, not 'human'; HIL "
            "submission applies only to human-actor nodes"
        )

    # 2. Verify the var_name is one of the node's declared outputs.
    output_var_to_slot: dict[str, tuple[str, bool]] = {}
    for output_name, ref in node.outputs.items():
        slot = output_name[:-1] if output_name.endswith("?") else output_name
        optional = output_name.endswith("?")
        v = ref.lstrip("$").split(".", 1)[0]
        output_var_to_slot[v] = (slot, optional)
    if var_name not in output_var_to_slot:
        raise HilSubmissionError(
            f"node {node_id!r} does not declare output for variable "
            f"${var_name!r}. Declared outputs: {list(output_var_to_slot)}"
        )
    if var_name not in workflow.variables:
        raise HilSubmissionError(f"variable ${var_name!r} not in workflow variables")
    type_name = workflow.variables[var_name].type
    type_obj = get_type(type_name)

    # 3. Locate the pending marker (by node_id + optional iter_path).
    resolved_iter_path = _resolve_marker_iter_path(
        job_slug=job_slug, node_id=node_id, iter_path=iter_path, root=root
    )
    marker_path = pending_marker_path(job_slug, node_id, resolved_iter_path, root=root)
    if not marker_path.is_file():
        raise HilSubmissionError(f"no pending HIL gate for node {node_id!r}")

    target_path = paths.variable_envelope_path(job_slug, var_name, resolved_iter_path, root=root)
    paths.variables_dir(job_slug, root=root).mkdir(parents=True, exist_ok=True)

    # 4. Stage the raw payload at the attempt's output.json so the type's
    # produce can read it via attempt_output_path(). For HIL nodes the
    # most-recent attempt directory is the active one — pick the highest
    # numbered run/ subdir under the iter-keyed nodes/<id>/<iter>/runs/.
    runs_dir = paths.node_runs_dir(job_slug, node_id, resolved_iter_path, root=root)
    runs_dir.mkdir(parents=True, exist_ok=True)
    attempt_no = _next_or_existing_attempt(runs_dir)
    attempt_dir = paths.node_attempt_dir(
        job_slug, node_id, attempt_no, resolved_iter_path, root=root
    )
    attempt_dir.mkdir(parents=True, exist_ok=True)
    output_json_path = attempt_dir / "output.json"
    atomic_write_text(output_json_path, json.dumps(value_payload))

    # 4b. Best-effort resolve the node's declared inputs so human-actor
    # types like pr-review-verdict can read upstream variables (the
    # linked PR URL) via ctx.inputs.
    from engine.v1 import resolver as _resolver

    inputs_map: dict[str, Any] = {}
    for input_name, ref in node.inputs.items():
        slot = input_name[:-1] if input_name.endswith("?") else input_name
        try:
            envelope = _resolver._read_loop_or_plain_envelope(
                ref=ref,
                job_slug=job_slug,
                root=root,
                iter_path=resolved_iter_path,
            )
        except _resolver.ResolutionError:
            continue
        if envelope is None:
            continue
        try:
            value: Any = _resolver._materialise_value(envelope)
            fields = _resolver._field_path_for_ref(ref)
            if fields:
                value = _resolver._walk_field_path(value, fields, ref)
        except _resolver.ResolutionError:
            continue
        inputs_map[slot] = value

    @dataclass
    class _Ctx:
        var_name: str
        job_dir: Path
        iter_path: tuple[int, ...]
        attempt_dir: Path
        inputs: dict[str, Any]

        def expected_path(self) -> Path:
            slug = self.job_dir.name
            root = self.job_dir.parent.parent
            return paths.variable_envelope_path(slug, self.var_name, self.iter_path, root=root)

        def attempt_output_path(self) -> Path:
            return self.attempt_dir / "output.json"

    ctx = _Ctx(
        var_name=var_name,
        job_dir=paths.job_dir(job_slug, root=root),
        iter_path=resolved_iter_path,
        attempt_dir=attempt_dir,
        inputs=inputs_map,
    )
    try:
        validated = type_obj.produce(type_obj.Decl(), ctx)
    except VariableTypeError as exc:
        # Verification failed — clean up the unverified payload so the
        # human can retry without confusion.
        if output_json_path.exists():
            output_json_path.unlink()
        raise HilSubmissionError(
            f"submission rejected by {type_name!r} verification: {exc}"
        ) from exc

    # 5. Wrap in envelope and persist atomically at the iter-keyed
    # variable path.
    env = make_envelope(
        type_name=type_name,
        producer_node=node_id,
        value_payload=validated.model_dump(mode="json"),
    )
    atomic_write_text(target_path, env.model_dump_json())

    # 6. If all required outputs have been submitted, remove the marker
    # so the driver can advance past this gate.
    if _all_required_outputs_submitted(
        node=node,
        output_var_to_slot=output_var_to_slot,
        job_slug=job_slug,
        root=root,
        iter_path=resolved_iter_path,
    ):
        remove_pending_marker(job_slug, node_id, resolved_iter_path, root=root)


def _resolve_marker_iter_path(
    *,
    job_slug: str,
    node_id: str,
    iter_path: tuple[int, ...] | None,
    root: Path,
) -> tuple[int, ...]:
    """Return the iter_path the marker for *node_id* sits at.

    If *iter_path* is supplied explicitly, return it verbatim. Otherwise
    scan the pending dir for files of the form ``<node_id>__<token>.json``
    and use the unique match; if multiple match, raise."""
    if iter_path is not None:
        return iter_path
    pdir = pending_dir(job_slug, root=root)
    if not pdir.is_dir():
        return ()
    prefix = f"{node_id}__"
    matches: list[tuple[int, ...]] = []
    for f in pdir.iterdir():
        if not f.name.startswith(prefix) or not f.name.endswith(".json"):
            continue
        token = f.name[len(prefix) : -len(".json")]
        try:
            matches.append(paths.parse_iter_token(token))
        except ValueError:
            continue
    if not matches:
        return ()
    if len(matches) > 1:
        raise HilSubmissionError(
            f"multiple pending markers for node {node_id!r} "
            f"({len(matches)} iter_paths); pass iter_path to disambiguate"
        )
    return matches[0]


def _next_or_existing_attempt(runs_dir: Path) -> int:
    """Pick the highest existing numbered attempt under *runs_dir*, or
    1 if none exist. HIL submissions reuse the agent's last attempt
    directory so chat.jsonl + output.json sit together."""
    attempts: list[int] = []
    if runs_dir.is_dir():
        for child in runs_dir.iterdir():
            if child.is_dir() and child.name.isdigit():
                attempts.append(int(child.name))
    return max(attempts) if attempts else 1


def _all_required_outputs_submitted(
    *,
    node: ArtifactNode,
    output_var_to_slot: dict[str, tuple[str, bool]],
    job_slug: str,
    root: Path,
    iter_path: tuple[int, ...],
) -> bool:
    """A node's pending marker is removed only when every required output
    has been produced. Optional outputs the human chose to skip don't
    block."""
    for var_name, (_slot, optional) in output_var_to_slot.items():
        env_path = paths.variable_envelope_path(job_slug, var_name, iter_path, root=root)
        if not env_path.is_file() and not optional:
            return False
    return True


def wait_for_node_outputs(
    *,
    node: ArtifactNode,
    workflow: Workflow,
    job_slug: str,
    root: Path,
    iter_path: tuple[int, ...] = (),
    poll_interval_seconds: float = 1.0,
    timeout_seconds: float | None = None,
) -> bool:
    """Block the calling thread until the node's pending marker is gone
    (i.e., every required output has been submitted).

    Returns True on success; False on timeout. The driver uses this to
    wait in-process for the human."""
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    marker = pending_marker_path(job_slug, node.id, iter_path, root=root)
    while True:
        if not marker.exists():
            return True
        if deadline is not None and time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval_seconds)
