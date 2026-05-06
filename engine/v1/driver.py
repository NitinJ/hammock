"""Workflow driver — orchestrates the lifecycle of a single Hammock v1 job.

T1 scope:
- ``submit_job``: validate workflow, create job dir layout, write JobConfig
  (state=SUBMITTED), seed the job-request variable on disk.
- ``run_job``: transition SUBMITTED → RUNNING, topologically iterate nodes
  by `after:` edges, dispatch each, persist NodeRun + variable envelopes,
  transition RUNNING → COMPLETED on success, RUNNING → FAILED on failure.

T2+ adds: HIL gate handling, code-kind dispatch, loop iteration, retries
beyond `max: 0`, crash recovery on driver (re)start.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from engine.v1 import predicate as _predicate
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
from engine.v1.loader import load_workflow
from engine.v1.loop_dispatch import dispatch_loop
from engine.v1.substrate import (
    JobRepo,
    SubstrateError,
    allocate_code_substrate,
    copy_local_repo,
    set_up_job_repo,
)
from engine.v1.validator import assert_valid
from shared.atomic import atomic_write_text
from shared.v1 import paths
from shared.v1.envelope import make_envelope
from shared.v1.job import (
    JobConfig,
    JobState,
    NodeRun,
    NodeRunState,
    make_job_config,
    make_node_run,
)
from shared.v1.workflow import ArtifactNode, CodeNode, LoopNode, Workflow

log = logging.getLogger(__name__)


class JobSubmissionError(Exception):
    """Raised when submit_job can't create a clean starting state."""


class DriverError(Exception):
    """Raised when run_job encounters an unrecoverable wiring problem
    (cycle in DAG, etc.). Per-node contract failures don't raise — they
    transition the job to FAILED and return."""


# ---------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------


def submit_job(
    *,
    workflow_path: Path,
    request_text: str,
    job_slug: str,
    root: Path,
    repo_url: str | None = None,
    repo_slug: str | None = None,
    repo_path: Path | None = None,
    default_branch: str = "main",
) -> JobConfig:
    """Create a fresh job dir + write JobConfig + seed job-request.

    Validates the workflow before creating any state on disk. If the
    workflow contains any `code`-kind nodes, also clones the test repo
    into ``<job_dir>/repo`` and creates the job branch (``hammock/jobs/<slug>``)
    off ``main`` — substrate setup that subsequent code-node dispatches
    fork from."""
    workflow = load_workflow(workflow_path)
    assert_valid(workflow)

    paths.ensure_job_layout(job_slug, root=root)

    cfg = make_job_config(
        job_slug=job_slug,
        workflow_name=workflow.workflow,
        workflow_path=workflow_path.resolve(),
        repo_slug=repo_slug,
    )
    atomic_write_text(paths.job_config_path(job_slug, root=root), cfg.model_dump_json(indent=2))

    # Seed the job-request typed variable so the first node can consume it.
    if "request" in workflow.variables:
        if workflow.variables["request"].type != "job-request":
            raise JobSubmissionError(
                "the variable named 'request' must have type 'job-request' "
                "(or rename your input variable)"
            )
        env = make_envelope(
            type_name="job-request",
            producer_node="<engine>",
            value_payload={"text": request_text},
        )
        atomic_write_text(
            paths.variable_envelope_path(job_slug, "request", root=root),
            env.model_dump_json(),
        )

    # Code-substrate set-up: when the workflow has any code-kind nodes
    # (top-level or inside a loop body) we need a working clone in
    # ``<job_dir>/repo``. Two paths during the path-only migration:
    #
    # - ``repo_path`` set: copy from the operator's registered local
    #   checkout. Per ``docs/projects-management.md`` this is the only
    #   intended path going forward — preserves ``.env`` and other
    #   uncommitted state the project needs to run.
    # - ``repo_url`` set (deprecated): clone-from-remote. Kept until
    #   all call sites migrate, then deleted.
    needs_repo = _has_code_node(workflow.nodes)
    if needs_repo:
        if repo_path is not None:
            if not repo_slug:
                raise JobSubmissionError(
                    "workflow contains code-kind nodes; submit_job requires "
                    "`repo_slug` when `repo_path` is set"
                )
            try:
                copy_local_repo(
                    job_slug=job_slug,
                    root=root,
                    repo_path=repo_path,
                    repo_slug=repo_slug,
                    default_branch=default_branch,
                )
            except SubstrateError as exc:
                raise JobSubmissionError(f"could not set up job repo: {exc}") from exc
        elif repo_url and repo_slug:
            try:
                set_up_job_repo(
                    job_slug=job_slug,
                    root=root,
                    repo_url=repo_url,
                    repo_slug=repo_slug,
                )
            except SubstrateError as exc:
                raise JobSubmissionError(f"could not set up job repo: {exc}") from exc
        else:
            raise JobSubmissionError(
                "workflow contains code-kind nodes; submit_job requires "
                "either `repo_path` (preferred) or `repo_url` + `repo_slug`"
            )

    return cfg


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def run_job(
    *,
    job_slug: str,
    root: Path,
    claude_runner: ClaudeRunner | None = None,
    code_claude_runner: CodeClaudeRunner | None = None,
    hil_poll_interval_seconds: float = 1.0,
    hil_timeout_seconds: float | None = None,
) -> JobConfig:
    """Drive the job to a terminal state. Returns the final JobConfig.

    For human-actor nodes the driver writes a pending marker and waits
    in-process (per design-patch §3.2 — driver never exits) until the
    submission API removes the marker. ``hil_timeout_seconds`` bounds
    the wait per HIL gate; ``None`` means wait forever (production
    default; tests pass a finite value)."""
    cfg_path = paths.job_config_path(job_slug, root=root)
    if not cfg_path.is_file():
        raise DriverError(f"job config not found at {cfg_path}")
    cfg = JobConfig.model_validate_json(cfg_path.read_text())
    workflow = load_workflow(Path(cfg.workflow_path))

    # Transition SUBMITTED → RUNNING (idempotent on resume).
    if cfg.state == JobState.SUBMITTED:
        cfg = _persist_state(cfg, JobState.RUNNING, root=root)

    order = _topological_order(workflow)
    log.info("driver: %d nodes in order %s", len(order), [n.id for n in order])

    # Lazily-built JobRepo handle. set_up_job_repo was called at submit
    # time; here we just rebuild the in-memory descriptor.
    job_repo: JobRepo | None = None
    if cfg.repo_slug is not None:
        job_repo = JobRepo(
            repo_dir=paths.repo_clone_dir(job_slug, root=root),
            repo_slug=cfg.repo_slug,
            job_branch=paths.job_branch_name(job_slug),
        )

    for node in order:
        # Skip nodes already SUCCEEDED (resume after crash).
        existing = _read_node_run(job_slug, node.id, root=root)
        if existing and existing.state in {
            NodeRunState.SUCCEEDED,
            NodeRunState.SKIPPED,
        }:
            log.info("node %s already %s — skipping", node.id, existing.state.value)
            continue

        if not isinstance(node, ArtifactNode | CodeNode | LoopNode):
            raise DriverError(f"node {node.id!r} has unsupported kind {type(node).__name__}")

        # runs_if: skip the node when the predicate evaluates false. Per
        # design-patch §1.6 rule 3, a skipped node produces no outputs;
        # downstream `after:` edges treat SKIPPED identically to SUCCEEDED.
        runs_if = getattr(node, "runs_if", None)
        if runs_if is not None:
            try:
                holds = _predicate.evaluate(
                    runs_if,
                    workflow=workflow,
                    job_slug=job_slug,
                    root=root,
                    current_iteration=None,
                )
            except _predicate.PredicateError as exc:
                _persist_node_run(
                    make_node_run(node.id),
                    state=NodeRunState.FAILED,
                    attempts=(existing.attempts if existing else 0) + 1,
                    last_error=f"runs_if predicate failed to parse/eval: {exc}",
                    job_slug=job_slug,
                    root=root,
                )
                cfg = _persist_state(cfg, JobState.FAILED, root=root)
                return cfg
            if not holds:
                log.info(
                    "node %s: runs_if=%r evaluated false — SKIPPED",
                    node.id,
                    runs_if,
                )
                _persist_node_run(
                    make_node_run(node.id),
                    state=NodeRunState.SKIPPED,
                    attempts=(existing.attempts if existing else 0) + 1,
                    job_slug=job_slug,
                    root=root,
                )
                continue

        # Run node.
        attempt = (existing.attempts if existing else 0) + 1
        _persist_node_run(
            make_node_run(node.id),
            state=NodeRunState.RUNNING,
            attempts=attempt,
            job_slug=job_slug,
            root=root,
        )

        # ---- loop kind ------------------------------------------------
        if isinstance(node, LoopNode):
            loop_result = dispatch_loop(
                node=node,
                workflow=workflow,
                job_slug=job_slug,
                root=root,
                job_repo=job_repo,
                artifact_claude_runner=claude_runner,
                code_claude_runner=code_claude_runner,
                hil_poll_interval_seconds=hil_poll_interval_seconds,
                hil_timeout_seconds=hil_timeout_seconds,
            )
            if not loop_result.succeeded:
                _persist_node_run(
                    make_node_run(node.id),
                    state=NodeRunState.FAILED,
                    attempts=attempt,
                    last_error=loop_result.error,
                    job_slug=job_slug,
                    root=root,
                )
                cfg = _persist_state(cfg, JobState.FAILED, root=root)
                return cfg
            _persist_node_run(
                make_node_run(node.id),
                state=NodeRunState.SUCCEEDED,
                attempts=attempt,
                job_slug=job_slug,
                root=root,
            )
            continue

        # ---- code kind ------------------------------------------------
        if isinstance(node, CodeNode):
            if job_repo is None:
                raise DriverError(
                    f"node {node.id!r} is code-kind but the job has no "
                    "repo_slug; submit_job should have set this up"
                )
            try:
                substrate = allocate_code_substrate(
                    job_slug=job_slug,
                    node_id=node.id,
                    root=root,
                    job_repo=job_repo,
                )
            except SubstrateError as exc:
                _persist_node_run(
                    make_node_run(node.id),
                    state=NodeRunState.FAILED,
                    attempts=attempt,
                    last_error=f"substrate allocation failed: {exc}",
                    job_slug=job_slug,
                    root=root,
                )
                cfg = _persist_state(cfg, JobState.FAILED, root=root)
                return cfg

            code_result = dispatch_code_agent(
                node=node,
                workflow=workflow,
                job_slug=job_slug,
                root=root,
                substrate=substrate,
                attempt=attempt,
                claude_runner=code_claude_runner,
            )
            if not code_result.succeeded:
                log.warning(
                    "code node %s failed: %s — marking job FAILED",
                    node.id,
                    code_result.error,
                )
                _persist_node_run(
                    make_node_run(node.id),
                    state=NodeRunState.FAILED,
                    attempts=attempt,
                    last_error=code_result.error,
                    job_slug=job_slug,
                    root=root,
                )
                cfg = _persist_state(cfg, JobState.FAILED, root=root)
                return cfg
            _persist_node_run(
                make_node_run(node.id),
                state=NodeRunState.SUCCEEDED,
                attempts=attempt,
                job_slug=job_slug,
                root=root,
            )
            continue

        # ---- artifact kind below --------------------------------------
        if node.actor == "human":
            ok = _dispatch_human_node(
                node=node,
                workflow=workflow,
                job_slug=job_slug,
                root=root,
                cfg=cfg,
                poll_interval_seconds=hil_poll_interval_seconds,
                timeout_seconds=hil_timeout_seconds,
            )
            if not ok:
                log.warning("node %s timed out waiting for HIL submission", node.id)
                _persist_node_run(
                    make_node_run(node.id),
                    state=NodeRunState.FAILED,
                    attempts=attempt,
                    last_error="timed out waiting for human submission",
                    job_slug=job_slug,
                    root=root,
                )
                cfg = _persist_state(cfg, JobState.FAILED, root=root)
                return cfg
            _persist_node_run(
                make_node_run(node.id),
                state=NodeRunState.SUCCEEDED,
                attempts=attempt,
                job_slug=job_slug,
                root=root,
            )
            # If we were BLOCKED_ON_HUMAN, return to RUNNING for downstream.
            if cfg.state == JobState.BLOCKED_ON_HUMAN:
                cfg = _persist_state(cfg, JobState.RUNNING, root=root)
            continue

        result = dispatch_artifact_agent(
            node=node,
            workflow=workflow,
            job_slug=job_slug,
            root=root,
            attempt=attempt,
            claude_runner=claude_runner,
        )
        if not result.succeeded:
            log.warning("node %s failed: %s — marking job FAILED", node.id, result.error)
            _persist_node_run(
                make_node_run(node.id),
                state=NodeRunState.FAILED,
                attempts=attempt,
                last_error=result.error,
                job_slug=job_slug,
                root=root,
            )
            cfg = _persist_state(cfg, JobState.FAILED, root=root)
            return cfg
        _persist_node_run(
            make_node_run(node.id),
            state=NodeRunState.SUCCEEDED,
            attempts=attempt,
            job_slug=job_slug,
            root=root,
        )

    cfg = _persist_state(cfg, JobState.COMPLETED, root=root)
    return cfg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dispatch_human_node(
    *,
    node: ArtifactNode,
    workflow: Workflow,
    job_slug: str,
    root: Path,
    cfg: JobConfig,
    poll_interval_seconds: float,
    timeout_seconds: float | None,
) -> bool:
    """Write a pending marker, transition to BLOCKED_ON_HUMAN, wait for
    the submission API to land the typed value(s) on disk, return True
    on success or False on timeout.

    Submission verification is the API's job (`engine.v1.hil.submit_hil_answer`
    runs the type's `produce` synchronously). By the time the marker is
    gone, the typed envelopes are already on disk and validated."""
    write_pending_marker(job_slug=job_slug, node=node, workflow=workflow, root=root)
    _persist_state(cfg, JobState.BLOCKED_ON_HUMAN, root=root)
    log.info("node %s blocked on human; pending marker written, waiting...", node.id)
    return wait_for_node_outputs(
        node=node,
        workflow=workflow,
        job_slug=job_slug,
        root=root,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
    )


def _has_code_node(nodes: list) -> bool:
    """True iff *nodes* (or any nested loop body) contains a CodeNode."""
    for n in nodes:
        if isinstance(n, CodeNode):
            return True
        if isinstance(n, LoopNode) and _has_code_node(list(n.body)):
            return True
    return False


def _topological_order(workflow: Workflow) -> list:
    """Kahn's algorithm — order nodes so every `after:` predecessor comes
    before its successor."""
    by_id: dict[str, ArtifactNode] = {n.id: n for n in workflow.nodes}
    in_degree: dict[str, int] = dict.fromkeys(by_id, 0)
    children: dict[str, list[str]] = {nid: [] for nid in by_id}
    for n in workflow.nodes:
        for parent in n.after:
            in_degree[n.id] += 1
            children.setdefault(parent, []).append(n.id)

    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    queue.sort()  # deterministic iteration
    order: list[ArtifactNode] = []
    while queue:
        nid = queue.pop(0)
        order.append(by_id[nid])
        for child in children.get(nid, []):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)
        queue.sort()

    if len(order) != len(by_id):
        unprocessed = [nid for nid, deg in in_degree.items() if deg > 0]
        raise DriverError(
            f"cycle detected in workflow (validator should have caught this); "
            f"unprocessed nodes: {unprocessed}"
        )
    return order


def _persist_state(cfg: JobConfig, new_state: JobState, *, root: Path) -> JobConfig:
    updated = cfg.model_copy(update={"state": new_state, "updated_at": datetime.now(UTC)})
    atomic_write_text(
        paths.job_config_path(cfg.job_slug, root=root),
        updated.model_dump_json(indent=2),
    )
    return updated


def _read_node_run(job_slug: str, node_id: str, *, root: Path) -> NodeRun | None:
    p = paths.node_state_path(job_slug, node_id, root=root)
    if not p.is_file():
        return None
    return NodeRun.model_validate_json(p.read_text())


def _persist_node_run(
    base: NodeRun,
    *,
    state: NodeRunState,
    attempts: int,
    job_slug: str,
    root: Path,
    last_error: str | None = None,
) -> None:
    now = datetime.now(UTC)
    updated = base.model_copy(
        update={
            "state": state,
            "attempts": attempts,
            "last_error": last_error,
            "started_at": base.started_at or now
            if state == NodeRunState.RUNNING
            else base.started_at,
            "finished_at": now
            if state
            in {
                NodeRunState.SUCCEEDED,
                NodeRunState.FAILED,
                NodeRunState.SKIPPED,
            }
            else base.finished_at,
        }
    )
    p = paths.node_state_path(job_slug, base.node_id, root=root)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, updated.model_dump_json(indent=2))
