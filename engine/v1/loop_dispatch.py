"""Loop-node dispatcher.

Per design-patch §5. T5 scope: ``until`` and ``count`` loops; nested
loops; substrate ``shared`` and ``per-iteration``; output projection
including ``[*]`` aggregation into ``list[T]``.

Body iteration:

1. Pre-loop: if any body node (transitively) is `code` kind and
   substrate is `shared`, allocate the code substrate once. For
   `per-iteration`, allocation happens per iter inside the loop body.
2. For each iteration:
   a. Run each body node in declared order (LoopNode body → recurse
      into ``dispatch_loop`` with the current iter as parent context).
   b. After the body completes, evaluate ``until`` (if any) — exit on true.
   c. For ``count`` loops, advance.
3. If ``until`` is exhausted without satisfying the predicate, return
   failure.
4. Post-loop: project body-produced indexed envelopes into either the
   plain workflow variable space (top-level loop) or the parent loop's
   indexed-variable space (nested loop), per the loop's ``outputs:``
   block. Supports ``[last]`` (scalar), ``[*]`` (list[T]), and
   ``[<int>]`` (specific iteration).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from engine.v1 import predicate
from engine.v1.artifact import ClaudeRunner, dispatch_artifact_agent
from engine.v1.code_dispatch import (
    ClaudeRunner as CodeClaudeRunner,
)
from engine.v1.code_dispatch import (
    dispatch_code_agent,
)
from engine.v1.hil import (
    wait_for_node_outputs,
    write_pending_marker,
)
from engine.v1.substrate import (
    CodeSubstrate,
    JobRepo,
    SubstrateError,
    allocate_code_substrate,
)
from shared.atomic import atomic_write_text
from shared.v1 import paths
from shared.v1.envelope import Envelope, make_envelope
from shared.v1.job import NodeRun, NodeRunState
from shared.v1.workflow import (
    ArtifactNode,
    CodeNode,
    LoopNode,
    Workflow,
)

log = logging.getLogger(__name__)


@dataclass
class LoopDispatchResult:
    succeeded: bool
    iterations_run: int
    error: str | None = None


# ``parent_loop_context`` carries the enclosing loop's id and iteration
# when this dispatch is for a nested inner loop, so output projection
# routes to the indexed envelope path of the outer loop instead of plain.
@dataclass(frozen=True)
class ParentLoopContext:
    loop_id: str
    iteration: int


def dispatch_loop(
    *,
    node: LoopNode,
    workflow: Workflow,
    job_slug: str,
    root: Path,
    job_repo: JobRepo | None,
    artifact_claude_runner: ClaudeRunner | None = None,
    code_claude_runner: CodeClaudeRunner | None = None,
    hil_poll_interval_seconds: float = 1.0,
    hil_timeout_seconds: float | None = None,
    parent_loop_context: ParentLoopContext | None = None,
    workflow_dir: Path | None = None,
) -> LoopDispatchResult:
    """Iterate ``node.body`` per the loop's count/until config."""
    # Resolve the loop kind: count vs until.
    if node.count is not None and node.until is not None:
        return LoopDispatchResult(
            succeeded=False,
            iterations_run=0,
            error=f"loop {node.id!r} declares both `count` and `until`; pick one",
        )
    if node.count is None and node.until is None:
        return LoopDispatchResult(
            succeeded=False,
            iterations_run=0,
            error=f"loop {node.id!r} declares neither `count` nor `until`",
        )

    is_count_loop = node.count is not None
    if is_count_loop:
        try:
            count_value = _resolve_count(
                node.count, workflow=workflow, job_slug=job_slug, root=root
            )
        except _LoopError as exc:
            return LoopDispatchResult(succeeded=False, iterations_run=0, error=str(exc))
        if count_value < 0:
            return LoopDispatchResult(
                succeeded=False,
                iterations_run=0,
                error=(f"loop {node.id!r}: count resolved to {count_value} (must be >= 0)"),
            )
        max_iters = count_value
        substrate_default = "per-iteration"
    else:
        if node.max_iterations is None or node.max_iterations <= 0:
            return LoopDispatchResult(
                succeeded=False,
                iterations_run=0,
                error="`until` loops require `max_iterations` >= 1",
            )
        max_iters = node.max_iterations
        substrate_default = "shared"

    substrate_mode = node.substrate or substrate_default

    # Substrate planning: allocate up-front for `shared`; defer for
    # `per-iteration`. Allocation is needed only if the body has a
    # code-kind node *directly* (not via an inner loop, which manages
    # its own substrate).
    direct_code_body = [n for n in node.body if isinstance(n, CodeNode)]
    shared_substrate: CodeSubstrate | None = None
    if direct_code_body and substrate_mode == "shared":
        if job_repo is None:
            return LoopDispatchResult(
                succeeded=False,
                iterations_run=0,
                error=(
                    f"loop {node.id!r} contains code-kind body nodes but the job has no repo_slug"
                ),
            )
        try:
            shared_substrate = allocate_code_substrate(
                job_slug=job_slug,
                node_id=_scoped_id(node.id, parent_loop_context),
                root=root,
                job_repo=job_repo,
            )
        except SubstrateError as exc:
            return LoopDispatchResult(
                succeeded=False,
                iterations_run=0,
                error=f"loop substrate allocation failed: {exc}",
            )

    iterations_run = 0
    for iteration in range(max_iters):
        log.info(
            "loop %s: iteration %d/%d (kind=%s, substrate=%s)",
            node.id,
            iteration,
            max_iters,
            "count" if is_count_loop else "until",
            substrate_mode,
        )

        # Per-iteration substrate allocation (if any direct code body
        # node; nested inner loops manage their own).
        iter_substrate: CodeSubstrate | None = shared_substrate
        if direct_code_body and substrate_mode == "per-iteration":
            if job_repo is None:
                return LoopDispatchResult(
                    succeeded=False,
                    iterations_run=iteration,
                    error=(
                        f"loop {node.id!r} per-iteration substrate needs a "
                        "job_repo (workflow has code nodes but JobConfig has no repo_slug)"
                    ),
                )
            try:
                iter_substrate = allocate_code_substrate(
                    job_slug=job_slug,
                    node_id=f"{_scoped_id(node.id, parent_loop_context)}-{iteration}",
                    root=root,
                    job_repo=job_repo,
                )
            except SubstrateError as exc:
                return LoopDispatchResult(
                    succeeded=False,
                    iterations_run=iteration,
                    error=f"per-iteration substrate alloc failed: {exc}",
                )

        # Run body in declared order.
        for body_node in node.body:
            ok = _dispatch_body_node(
                body_node=body_node,
                loop_node=node,
                workflow=workflow,
                job_slug=job_slug,
                root=root,
                iteration=iteration,
                code_substrate=iter_substrate,
                job_repo=job_repo,
                artifact_claude_runner=artifact_claude_runner,
                code_claude_runner=code_claude_runner,
                hil_poll_interval_seconds=hil_poll_interval_seconds,
                hil_timeout_seconds=hil_timeout_seconds,
                workflow_dir=workflow_dir,
            )
            if not ok.succeeded:
                return LoopDispatchResult(
                    succeeded=False,
                    iterations_run=iteration,
                    error=ok.error,
                )
        iterations_run = iteration + 1

        # Until-loop: evaluate predicate after the body completes.
        if not is_count_loop:
            try:
                predicate_holds = predicate.evaluate(
                    node.until,
                    workflow=workflow,
                    job_slug=job_slug,
                    root=root,
                    current_iteration=iteration,
                )
            except predicate.PredicateError as exc:
                return LoopDispatchResult(
                    succeeded=False,
                    iterations_run=iterations_run,
                    error=f"predicate evaluation failed: {exc}",
                )
            if predicate_holds:
                log.info("loop %s: predicate satisfied at iter %d", node.id, iteration)
                _project_outputs(
                    loop_node=node,
                    final_iteration=iteration,
                    iterations_run=iterations_run,
                    job_slug=job_slug,
                    root=root,
                    parent_loop_context=parent_loop_context,
                )
                return LoopDispatchResult(succeeded=True, iterations_run=iterations_run)

    # Either count loop reached the end (success) or until exhausted (failure).
    if is_count_loop:
        _project_outputs(
            loop_node=node,
            final_iteration=iterations_run - 1 if iterations_run > 0 else 0,
            iterations_run=iterations_run,
            job_slug=job_slug,
            root=root,
            parent_loop_context=parent_loop_context,
        )
        return LoopDispatchResult(succeeded=True, iterations_run=iterations_run)

    return LoopDispatchResult(
        succeeded=False,
        iterations_run=iterations_run,
        error=(f"loop {node.id!r}: predicate never became true after {max_iters} iteration(s)"),
    )


# ---------------------------------------------------------------------------
# Body dispatch
# ---------------------------------------------------------------------------


@dataclass
class _BodyDispatchOk:
    succeeded: bool
    error: str | None = None


def _dispatch_body_node(
    *,
    body_node: ArtifactNode | CodeNode | LoopNode,
    loop_node: LoopNode,
    workflow: Workflow,
    job_slug: str,
    root: Path,
    iteration: int,
    code_substrate: CodeSubstrate | None,
    job_repo: JobRepo | None,
    artifact_claude_runner: ClaudeRunner | None,
    code_claude_runner: CodeClaudeRunner | None,
    hil_poll_interval_seconds: float,
    hil_timeout_seconds: float | None,
    workflow_dir: Path | None = None,
) -> _BodyDispatchOk:
    if isinstance(body_node, LoopNode):
        # Nested loop: recurse with parent context = this loop's iter.
        # Body-node state.json doesn't apply here — the inner loop's own
        # body dispatches will handle their own persistence.
        result = dispatch_loop(
            node=body_node,
            workflow=workflow,
            job_slug=job_slug,
            root=root,
            job_repo=job_repo,
            artifact_claude_runner=artifact_claude_runner,
            code_claude_runner=code_claude_runner,
            hil_poll_interval_seconds=hil_poll_interval_seconds,
            hil_timeout_seconds=hil_timeout_seconds,
            parent_loop_context=ParentLoopContext(loop_id=loop_node.id, iteration=iteration),
            workflow_dir=workflow_dir,
        )
        return _BodyDispatchOk(succeeded=result.succeeded, error=result.error)

    # Mark RUNNING on entry. state.json is overwritten per iteration —
    # the dashboard's per-iter row state is refined client-side using
    # envelope existence, but the underlying row needs *some* state file
    # to exist so /api/jobs/{slug}/nodes/{id} doesn't 404.
    attempts = iteration + 1
    _persist_body_state(
        job_slug=job_slug,
        node_id=body_node.id,
        root=root,
        state=NodeRunState.RUNNING,
        attempts=attempts,
        started=True,
    )

    if isinstance(body_node, CodeNode):
        if code_substrate is None:
            _persist_body_state(
                job_slug=job_slug,
                node_id=body_node.id,
                root=root,
                state=NodeRunState.FAILED,
                attempts=attempts,
                last_error="no substrate allocated for code body node",
            )
            return _BodyDispatchOk(
                succeeded=False,
                error=(f"loop body has code node {body_node.id!r} but no substrate was allocated"),
            )
        code_result = dispatch_code_agent(
            node=body_node,
            workflow=workflow,
            job_slug=job_slug,
            root=root,
            substrate=code_substrate,
            attempt=attempts,
            claude_runner=code_claude_runner,
            workflow_dir=workflow_dir,
            loop_id=loop_node.id,
            iteration=iteration,
        )
        _persist_body_state(
            job_slug=job_slug,
            node_id=body_node.id,
            root=root,
            state=NodeRunState.SUCCEEDED if code_result.succeeded else NodeRunState.FAILED,
            attempts=attempts,
            last_error=None if code_result.succeeded else code_result.error,
        )
        return _BodyDispatchOk(succeeded=code_result.succeeded, error=code_result.error)

    # Artifact body node — agent or human actor.
    if body_node.actor == "human":
        write_pending_marker(
            job_slug=job_slug,
            node=body_node,
            workflow=workflow,
            root=root,
            loop_id=loop_node.id,
            iteration=iteration,
        )
        ok = wait_for_node_outputs(
            node=body_node,
            workflow=workflow,
            job_slug=job_slug,
            root=root,
            poll_interval_seconds=hil_poll_interval_seconds,
            timeout_seconds=hil_timeout_seconds,
        )
        if not ok:
            _persist_body_state(
                job_slug=job_slug,
                node_id=body_node.id,
                root=root,
                state=NodeRunState.FAILED,
                attempts=attempts,
                last_error="timed out waiting for human submission",
            )
            return _BodyDispatchOk(
                succeeded=False,
                error=(f"loop body {body_node.id!r} timed out waiting for HIL submission"),
            )
        _persist_body_state(
            job_slug=job_slug,
            node_id=body_node.id,
            root=root,
            state=NodeRunState.SUCCEEDED,
            attempts=attempts,
        )
        return _BodyDispatchOk(succeeded=True)

    # Artifact + agent.
    result = dispatch_artifact_agent(
        node=body_node,
        workflow=workflow,
        job_slug=job_slug,
        root=root,
        attempt=attempts,
        claude_runner=artifact_claude_runner,
        workflow_dir=workflow_dir,
        loop_id=loop_node.id,
        iteration=iteration,
    )
    _persist_body_state(
        job_slug=job_slug,
        node_id=body_node.id,
        root=root,
        state=NodeRunState.SUCCEEDED if result.succeeded else NodeRunState.FAILED,
        attempts=attempts,
        last_error=None if result.succeeded else result.error,
    )
    return _BodyDispatchOk(succeeded=result.succeeded, error=result.error)


def _persist_body_state(
    *,
    job_slug: str,
    node_id: str,
    root: Path,
    state: NodeRunState,
    attempts: int,
    last_error: str | None = None,
    started: bool = False,
) -> None:
    """Write/overwrite ``nodes/<node_id>/state.json`` for a loop body node.

    Per ``docs/projects-management.md``-era dashboard contract: the
    dashboard's per-node detail endpoint (``GET /api/jobs/{slug}/nodes/
    {id}``) reads this file. Without it, clicking a body row 404s and
    the row appears stuck pending. The file reflects the *latest*
    iteration; per-iter row state is refined client-side from envelope
    existence."""
    sp = paths.node_state_path(job_slug, node_id, root=root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)

    # Preserve started_at across iterations so the dashboard timestamp
    # reflects when the engine first reached this body node.
    started_at: datetime | None = now if started else None
    finished_at: datetime | None = (
        now
        if state in {NodeRunState.SUCCEEDED, NodeRunState.FAILED, NodeRunState.SKIPPED}
        else None
    )
    if sp.is_file():
        try:
            existing = NodeRun.model_validate_json(sp.read_text())
        except Exception:
            existing = None
        if existing is not None:
            started_at = existing.started_at or started_at

    nr = NodeRun(
        node_id=node_id,
        state=state,
        attempts=attempts,
        last_error=last_error,
        started_at=started_at,
        finished_at=finished_at,
    )
    atomic_write_text(sp, nr.model_dump_json(indent=2))


# ---------------------------------------------------------------------------
# Output projection
# ---------------------------------------------------------------------------


_OUTPUT_REF_RE = re.compile(
    r"^\$(?P<loop_id>[a-zA-Z][a-zA-Z0-9_-]*)\."
    r"(?P<var>[a-zA-Z_][a-zA-Z0-9_]*)"
    r"\[(?P<idx>last|\*|\d+)\]"
    r"$"
)


def _project_outputs(
    *,
    loop_node: LoopNode,
    final_iteration: int,
    iterations_run: int,
    job_slug: str,
    root: Path,
    parent_loop_context: ParentLoopContext | None,
) -> None:
    """Project this loop's body-produced indexed envelopes into the
    appropriate target path per ``outputs:``.

    Target path layout:
    - Top-level loop: plain ``<external_name>.json``.
    - Nested inner loop: indexed under outer loop's scope —
      ``loop_<outer-id>_<external_name>_<outer-iter>.json``.

    Reference forms supported in ``outputs:`` values:
    - ``$loop-id.var[last]`` — final iteration's value (scalar T).
    - ``$loop-id.var[*]``    — all iterations as ``list[T]``.
    - ``$loop-id.var[<int>]`` — specific iteration (scalar T).
    """
    for external_name, ref in loop_node.outputs.items():
        m = _OUTPUT_REF_RE.match(ref.strip())
        if m is None:
            log.warning(
                "loop %s: output projection for %s has unrecognised ref %r — skipping",
                loop_node.id,
                external_name,
                ref,
            )
            continue
        ref_loop_id = m.group("loop_id")
        body_var = m.group("var")
        idx_form = m.group("idx")

        if ref_loop_id != loop_node.id:
            log.warning(
                "loop %s: output ref %s targets a different loop id %r — skipping",
                loop_node.id,
                ref,
                ref_loop_id,
            )
            continue

        if idx_form == "last":
            envelope_text = _read_last_envelope_text(
                job_slug=job_slug,
                loop_id=loop_node.id,
                var_name=body_var,
                final_iteration=final_iteration,
                root=root,
            )
            if envelope_text is None:
                log.warning(
                    "loop %s: [last] projection for %s found no envelope on disk",
                    loop_node.id,
                    body_var,
                )
                continue
            _write_projected(
                job_slug=job_slug,
                external_name=external_name,
                envelope_text=envelope_text,
                parent_loop_context=parent_loop_context,
                root=root,
            )
        elif idx_form == "*":
            list_envelope = _build_list_envelope(
                loop_node=loop_node,
                body_var=body_var,
                iterations_run=iterations_run,
                external_name=external_name,
                workflow_var_type=_workflow_var_type(external_name, loop_node, job_slug, root),
                job_slug=job_slug,
                root=root,
            )
            if list_envelope is None:
                continue
            _write_projected(
                job_slug=job_slug,
                external_name=external_name,
                envelope_text=list_envelope,
                parent_loop_context=parent_loop_context,
                root=root,
            )
        else:
            # Specific iteration index.
            k = int(idx_form)
            path = paths.loop_variable_envelope_path(job_slug, loop_node.id, body_var, k, root=root)
            if not path.is_file():
                log.warning(
                    "loop %s: [%d] projection for %s missing envelope at %s",
                    loop_node.id,
                    k,
                    body_var,
                    path,
                )
                continue
            _write_projected(
                job_slug=job_slug,
                external_name=external_name,
                envelope_text=path.read_text(),
                parent_loop_context=parent_loop_context,
                root=root,
            )


def _read_last_envelope_text(
    *,
    job_slug: str,
    loop_id: str,
    var_name: str,
    final_iteration: int,
    root: Path,
) -> str | None:
    """Read the highest-iteration envelope on disk for *var_name* in
    *loop_id*, capped at *final_iteration*."""
    for k in range(final_iteration, -1, -1):
        p = paths.loop_variable_envelope_path(job_slug, loop_id, var_name, k, root=root)
        if p.is_file():
            return p.read_text()
    return None


def _build_list_envelope(
    *,
    loop_node: LoopNode,
    body_var: str,
    iterations_run: int,
    external_name: str,
    workflow_var_type: str | None,
    job_slug: str,
    root: Path,
) -> str | None:
    """Read the indexed envelopes for *body_var* across iterations
    0..iterations_run-1 and produce a single ``list[T]`` envelope."""
    items: list[dict] = []
    inner_type_name: str | None = None
    repo: str | None = None
    for k in range(iterations_run):
        p = paths.loop_variable_envelope_path(job_slug, loop_node.id, body_var, k, root=root)
        if not p.is_file():
            log.warning(
                "loop %s: [*] aggregation skipping iter %d (%s missing)",
                loop_node.id,
                k,
                body_var,
            )
            continue
        env = Envelope.model_validate_json(p.read_text())
        items.append(env.value)
        if inner_type_name is None:
            inner_type_name = env.type
        if repo is None:
            repo = env.repo

    if inner_type_name is None and workflow_var_type is None:
        log.warning(
            "loop %s: [*] aggregation %s found no envelopes and no declared type — skipping",
            loop_node.id,
            body_var,
        )
        return None

    list_type_name = workflow_var_type or f"list[{inner_type_name}]"
    env = make_envelope(
        type_name=list_type_name,
        producer_node=f"<loop:{loop_node.id}>",
        value_payload=items,
        repo=repo,
    )
    return env.model_dump_json()


def _workflow_var_type(
    external_name: str,
    loop_node: LoopNode,
    job_slug: str,
    root: Path,
) -> str | None:
    """Look up the declared workflow variable type for *external_name*
    via the JobConfig's workflow path. We avoid passing Workflow into
    every helper by reading from the loop's outputs context. Best-effort:
    returns None if not findable, which lets callers fall back to a
    derived ``list[<inner>]`` name."""
    # The Workflow object isn't passed in here to keep the projection
    # function signature small; we look up the type via the registry
    # using the inner type discovered on disk. Returning None means the
    # caller derives ``list[<inner>]`` from envelope data.
    return None


def _write_projected(
    *,
    job_slug: str,
    external_name: str,
    envelope_text: str,
    parent_loop_context: ParentLoopContext | None,
    root: Path,
) -> None:
    """Write *envelope_text* either to plain or to outer-loop-indexed path."""
    if parent_loop_context is None:
        target = paths.variable_envelope_path(job_slug, external_name, root=root)
    else:
        target = paths.loop_variable_envelope_path(
            job_slug,
            parent_loop_context.loop_id,
            external_name,
            parent_loop_context.iteration,
            root=root,
        )
    paths.variables_dir(job_slug, root=root).mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, envelope_text)


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------


class _LoopError(Exception):
    """Internal: ``count`` resolution failure surfaced to dispatch."""


_COUNT_REF_RE = re.compile(r"^\$(?P<var>[a-zA-Z][a-zA-Z0-9_.\-]*)$")


def _resolve_count(raw: int | str, *, workflow: Workflow, job_slug: str, root: Path) -> int:
    """Resolve a literal int or ``$ref`` form to a concrete int."""
    if isinstance(raw, int):
        return raw
    if not isinstance(raw, str):
        raise _LoopError(f"count must be int or `$ref`, got {type(raw).__name__}")
    text = raw.strip()
    # Try literal int in string form.
    try:
        return int(text)
    except ValueError:
        pass
    # Otherwise treat as a predicate-style reference and read the value.
    try:
        parsed = predicate.parse_ref(text)
    except predicate.PredicateError as exc:
        raise _LoopError(f"could not parse count reference {text!r}: {exc}") from exc
    if parsed.loop_id is not None:
        # We're outside the referenced loop (this is a count source for
        # a sibling loop). Only `[last]` and explicit `[<int>]` make
        # sense here; `[i]` / `[i-1]` need an enclosing iteration
        # context which doesn't exist at dispatch time.
        if parsed.index_form == "last":
            highest = predicate._highest_loop_iteration(  # type: ignore[attr-defined]
                job_slug=job_slug,
                loop_id=parsed.loop_id,
                var_name=parsed.var_name,
                root=root,
            )
            if highest is None:
                raise _LoopError(
                    f"count ref {text!r}: [last] resolves to nothing "
                    f"(no envelopes on disk for {parsed.loop_id}.{parsed.var_name})"
                )
            iteration = highest
        elif parsed.index_form and parsed.index_form.isdigit():
            iteration = int(parsed.index_form)
        else:
            raise _LoopError(
                f"count ref {text!r}: index form {parsed.index_form!r} "
                "not legal here (use [last] or an integer literal)"
            )
        envelope = predicate._read_loop_envelope(  # type: ignore[attr-defined]
            job_slug=job_slug,
            loop_id=parsed.loop_id,
            var_name=parsed.var_name,
            iteration=iteration,
            root=root,
        )
    else:
        envelope = predicate._read_plain_envelope(  # type: ignore[attr-defined]
            job_slug=job_slug, var_name=parsed.var_name, root=root
        )
    if envelope is None:
        raise _LoopError(f"count ref {text!r} resolved to no envelope on disk")
    value = predicate._materialise_envelope_value(envelope)  # type: ignore[attr-defined]
    if parsed.field_path:
        try:
            value = predicate._walk_field_path(value, parsed.field_path, text)  # type: ignore[attr-defined]
        except predicate.PredicateError as exc:
            raise _LoopError(str(exc)) from exc
    if not isinstance(value, int):
        raise _LoopError(
            f"count ref {text!r} resolved to non-int value of type {type(value).__name__}"
        )
    return int(value)


def _scoped_id(node_id: str, parent: ParentLoopContext | None) -> str:
    """When dispatched as a nested loop, qualify the node id with the
    outer loop's iteration so substrate / branch names are unique per
    outer iter."""
    if parent is None:
        return node_id
    return f"{parent.loop_id}-{parent.iteration}-{node_id}"
