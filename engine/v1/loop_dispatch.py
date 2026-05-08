"""Loop-node dispatcher.

Per ``docs/loop-execution-model.md``. v2 keying: every body execution
identifies as ``(node_id, iter_path)`` where iter_path is a tuple of
ints, one per enclosing loop, outermost first. The dispatcher threads
``iter_path`` to every body node it runs.

Output projection:

- ``[last]`` / ``[i-1]`` / ``[<int>]`` → write a ``{"$ref": "<stem>"}``
  pointer file at the outer-scope path. Resolver follows one indirection.
- ``[*]`` aggregations → materialize the actual ``list[T]`` envelope at
  the outer-scope path (no single source to point at).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    workflow_dir: Path | None = None,
    iter_path: tuple[int, ...] = (),
) -> LoopDispatchResult:
    """Iterate ``node.body`` per the loop's count/until config.

    ``iter_path`` is the *enclosing* iter chain: empty tuple at top
    level, otherwise one int per outer loop. Each body iteration runs
    under ``iter_path + (iter,)``."""
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
                node_id=_scoped_id(node.id, iter_path),
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

        # Body's full iter chain.
        body_iter_path: tuple[int, ...] = (*iter_path, iteration)

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
                    node_id=f"{_scoped_id(node.id, iter_path)}-{iteration}",
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
                body_iter_path=body_iter_path,
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
                    iter_path=body_iter_path,
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
                    enclosing_iter_path=iter_path,
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
            enclosing_iter_path=iter_path,
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
    body_iter_path: tuple[int, ...],
    code_substrate: CodeSubstrate | None,
    job_repo: JobRepo | None,
    artifact_claude_runner: ClaudeRunner | None,
    code_claude_runner: CodeClaudeRunner | None,
    hil_poll_interval_seconds: float,
    hil_timeout_seconds: float | None,
    workflow_dir: Path | None = None,
) -> _BodyDispatchOk:
    """Dispatch a single body node under *body_iter_path*.

    *body_iter_path* is the full iter chain: enclosing iter_path
    extended by this loop's current iteration."""
    # Innermost iteration (this loop's index).
    iteration = body_iter_path[-1]
    if isinstance(body_node, LoopNode):
        # Nested loop: recurse, passing *body_iter_path* as the inner
        # loop's enclosing iter_path. Body-node state.json doesn't apply
        # here — the inner loop's own body dispatches handle persistence.
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
            workflow_dir=workflow_dir,
            iter_path=body_iter_path,
        )
        return _BodyDispatchOk(succeeded=result.succeeded, error=result.error)

    # Mark RUNNING on entry. State file lives at the iter-keyed path so
    # outer-iteration re-entries don't clobber prior iters' state.
    attempts = 1
    _persist_body_state(
        job_slug=job_slug,
        node_id=body_node.id,
        root=root,
        state=NodeRunState.RUNNING,
        attempts=attempts,
        iter_path=body_iter_path,
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
                iter_path=body_iter_path,
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
            iter_path=body_iter_path,
        )
        _persist_body_state(
            job_slug=job_slug,
            node_id=body_node.id,
            root=root,
            state=NodeRunState.SUCCEEDED if code_result.succeeded else NodeRunState.FAILED,
            attempts=attempts,
            iter_path=body_iter_path,
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
            iter_path=body_iter_path,
        )
        ok = wait_for_node_outputs(
            node=body_node,
            workflow=workflow,
            job_slug=job_slug,
            root=root,
            iter_path=body_iter_path,
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
                iter_path=body_iter_path,
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
            iter_path=body_iter_path,
        )
        del iteration  # consumed by the marker above
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
        repo_dir=job_repo.repo_dir if job_repo is not None else None,
        iter_path=body_iter_path,
    )
    _persist_body_state(
        job_slug=job_slug,
        node_id=body_node.id,
        root=root,
        state=NodeRunState.SUCCEEDED if result.succeeded else NodeRunState.FAILED,
        attempts=attempts,
        iter_path=body_iter_path,
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
    iter_path: tuple[int, ...],
    last_error: str | None = None,
    started: bool = False,
) -> None:
    """Write/overwrite ``nodes/<id>/<iter_token>/state.json`` for a loop
    body node.

    v2 keying: the state file sits under the iter token, so outer-loop
    re-entries don't clobber prior iterations' state. Each
    (node_id, iter_path) gets its own file."""
    sp = paths.node_state_path(job_slug, node_id, iter_path, root=root)
    sp.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)

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
    enclosing_iter_path: tuple[int, ...],
) -> None:
    """Project this loop's body-produced envelopes into the outer-scope
    path per ``outputs:``.

    The outer-scope path is ``variables/<external>__<token>.json`` where
    ``token = iter_token(enclosing_iter_path)``.

    - ``[last]`` / ``[i-1]`` / ``[<int>]`` → write a tiny ``$ref``
      pointer file at the outer path. Resolver follows one indirection.
    - ``[*]`` → materialize the actual ``list[T]`` envelope at the
      outer path.
    """
    paths.variables_dir(job_slug, root=root).mkdir(parents=True, exist_ok=True)
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

        target = paths.variable_envelope_path(
            job_slug, external_name, enclosing_iter_path, root=root
        )

        if idx_form == "last":
            source_iter = _highest_existing_iter(
                job_slug=job_slug,
                var_name=body_var,
                enclosing_iter_path=enclosing_iter_path,
                cap=final_iteration,
                root=root,
            )
            if source_iter is None:
                log.warning(
                    "loop %s: [last] projection for %s found no envelope on disk",
                    loop_node.id,
                    body_var,
                )
                continue
            stem = _envelope_stem(body_var, (*enclosing_iter_path, source_iter))
            atomic_write_text(target, json.dumps({"$ref": stem}))
        elif idx_form == "*":
            list_envelope = _build_list_envelope(
                loop_node=loop_node,
                body_var=body_var,
                iterations_run=iterations_run,
                enclosing_iter_path=enclosing_iter_path,
                job_slug=job_slug,
                root=root,
            )
            if list_envelope is None:
                continue
            atomic_write_text(target, list_envelope)
        else:
            k = int(idx_form)
            source_path = paths.variable_envelope_path(
                job_slug, body_var, (*enclosing_iter_path, k), root=root
            )
            if not source_path.is_file():
                log.warning(
                    "loop %s: [%d] projection for %s missing envelope at %s",
                    loop_node.id,
                    k,
                    body_var,
                    source_path,
                )
                continue
            stem = _envelope_stem(body_var, (*enclosing_iter_path, k))
            atomic_write_text(target, json.dumps({"$ref": stem}))


def _read_envelope_following_ref(path: Path) -> Envelope | None:
    """Read an envelope file, following one ``$ref`` indirection.

    Mirrors the resolver/predicate ref-follow logic; the projection
    helper needs it because a 2-deep nest's inner [*] aggregation
    reads files the outer's projection wrote as pointers.
    """
    raw = path.read_text()
    if not raw.strip():
        return None
    parsed = json.loads(raw)
    if isinstance(parsed, dict) and set(parsed.keys()) == {"$ref"}:
        stem = parsed["$ref"]
        if not isinstance(stem, str) or not stem:
            return None
        source = path.parent / f"{stem}.json"
        if not source.is_file():
            return None
        return Envelope.model_validate_json(source.read_text())
    return Envelope.model_validate_json(raw)


def _envelope_stem(var_name: str, iter_path: tuple[int, ...]) -> str:
    """Filename stem (without ``.json``) for the variable envelope at
    *iter_path*. Used as the ``$ref`` payload."""
    return f"{var_name}__{paths.iter_token(iter_path)}"


def _highest_existing_iter(
    *,
    job_slug: str,
    var_name: str,
    enclosing_iter_path: tuple[int, ...],
    cap: int,
    root: Path,
) -> int | None:
    """Find the highest iter index <= *cap* under *enclosing_iter_path*
    that has an envelope on disk for *var_name*."""
    for k in range(cap, -1, -1):
        p = paths.variable_envelope_path(job_slug, var_name, (*enclosing_iter_path, k), root=root)
        if p.is_file():
            return k
    return None


def _build_list_envelope(
    *,
    loop_node: LoopNode,
    body_var: str,
    iterations_run: int,
    enclosing_iter_path: tuple[int, ...],
    job_slug: str,
    root: Path,
) -> str | None:
    """Read the body envelopes for *body_var* across this loop's
    iterations and produce a single ``list[T]`` envelope.

    The result is materialised (not pointer'd): ``[*]`` aggregates have
    no single source to point at, so we serialise the actual list."""
    items: list[Any] = []
    inner_type_name: str | None = None
    repo: str | None = None
    for k in range(iterations_run):
        p = paths.variable_envelope_path(job_slug, body_var, (*enclosing_iter_path, k), root=root)
        if not p.is_file():
            log.warning(
                "loop %s: [*] aggregation skipping iter %d (%s missing)",
                loop_node.id,
                k,
                body_var,
            )
            continue
        # Aggregation may run over an inner loop's outputs that were
        # themselves projected as $ref pointer files; follow once to the
        # actual body envelope.
        env = _read_envelope_following_ref(p)
        if env is None:
            continue
        items.append(env.value)
        if inner_type_name is None:
            inner_type_name = env.type
        if repo is None:
            repo = env.repo

    if inner_type_name is None:
        log.warning(
            "loop %s: [*] aggregation %s found no envelopes — skipping",
            loop_node.id,
            body_var,
        )
        return None

    list_type_name = f"list[{inner_type_name}]"
    env = make_envelope(
        type_name=list_type_name,
        producer_node=f"<loop:{loop_node.id}>",
        value_payload=items,
        repo=repo,
    )
    return env.model_dump_json()


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
            highest = predicate._highest_loop_iteration(
                job_slug=job_slug,
                var_name=parsed.var_name,
                enclosing_iter_path=(),
                root=root,
            )
            if highest is None:
                raise _LoopError(
                    f"count ref {text!r}: [last] resolves to nothing "
                    f"(no envelopes on disk for {parsed.loop_id}.{parsed.var_name})"
                )
            iteration_idx = highest
        elif parsed.index_form and parsed.index_form.isdigit():
            iteration_idx = int(parsed.index_form)
        else:
            raise _LoopError(
                f"count ref {text!r}: index form {parsed.index_form!r} "
                "not legal here (use [last] or an integer literal)"
            )
        envelope = predicate._read_loop_envelope(
            job_slug=job_slug,
            var_name=parsed.var_name,
            iter_path=(iteration_idx,),
            root=root,
        )
    else:
        envelope = predicate._read_plain_envelope(
            job_slug=job_slug, var_name=parsed.var_name, root=root
        )
    if envelope is None:
        raise _LoopError(f"count ref {text!r} resolved to no envelope on disk")
    value = predicate._materialise_envelope_value(envelope)
    if parsed.field_path:
        try:
            value = predicate._walk_field_path(value, parsed.field_path, text)
        except predicate.PredicateError as exc:
            raise _LoopError(str(exc)) from exc
    if not isinstance(value, int):
        raise _LoopError(
            f"count ref {text!r} resolved to non-int value of type {type(value).__name__}"
        )
    return int(value)


def _scoped_id(node_id: str, enclosing_iter_path: tuple[int, ...]) -> str:
    """When dispatched as a nested loop, qualify the node id with the
    enclosing iter token so substrate / branch names are unique per
    outer iter."""
    if not enclosing_iter_path:
        return node_id
    return f"{node_id}-{paths.iter_token(enclosing_iter_path)}"
