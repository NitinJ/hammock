"""Pure-function projections — disk → response payload.

Per impl-patch §Stage 3: every projection is a pure function of
``(root: Path, ...) → response``. No in-memory cache, no side effects.

The dashboard's HTTP handlers call these to build response bodies; the
SSE handler subscribes to filesystem changes via ``dashboard/watcher/``
and resolves each fan-out by calling the relevant projection again.

All paths come from ``shared.v1.paths``. The disk layout is the v1
layout exclusively — v0 is gone.

v2 keying (loops-v2): every execution is identified by ``(node_id,
iter_path)`` where ``iter_path`` is a tuple of loop iteration indices,
outermost first. ``state.json`` lives at ``nodes/<id>/<iter_token>/``
and variable envelopes at ``variables/<var>__<iter_token>.json``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.v1.loader import WorkflowLoadError, load_workflow
from shared.v1 import paths as v1_paths
from shared.v1.envelope import Envelope
from shared.v1.job import JobConfig, JobState, NodeRun, NodeRunState
from shared.v1.workflow import ArtifactNode, CodeNode, LoopNode, Node, Workflow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ProjectListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    name: str
    repo_path: str
    remote_url: str | None = None
    default_branch: str | None = None
    open_jobs: int
    last_job_at: datetime | None
    last_health_check_at: datetime | None = None
    last_health_check_status: Literal["pass", "warn", "fail"] | None = None


class ProjectDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str
    name: str
    repo_path: str
    remote_url: str | None = None
    default_branch: str
    last_health_check_at: datetime | None = None
    last_health_check_status: Literal["pass", "warn", "fail"] | None = None


class JobListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_slug: str
    workflow_name: str
    state: JobState
    submitted_at: datetime
    updated_at: datetime
    repo_slug: str | None = None


class NodeListEntry(BaseModel):
    """One entry in the job-detail node list.

    For top-level nodes ``iter`` is empty; for loop body nodes it holds
    the iteration index list (one int per nesting level — ``[0]`` for a
    body node inside one loop, ``[0, 1]`` inside a nested loop). Loop
    nodes themselves are NOT emitted as rows; the frontend synthesises
    section headers from ``loop_path`` and ``iter`` on body rows.

    ``loop_path`` is parallel to ``iter`` — one loop_id per nesting
    level — so sibling loops at the same depth (whose iter indices
    coincide) can be distinguished by loop_id.
    """

    model_config = ConfigDict(extra="forbid")
    node_id: str
    name: str | None = None
    """Workflow's optional ``name:`` field — human-readable label.
    Frontend displays this in the node list and falls back to ``node_id``
    when absent. Loop body rows use the body node's name; the parent
    loop's name surfaces via ``JobDetail.loop_names``."""
    kind: Literal["artifact", "code"] | None = None
    actor: Literal["agent", "human", "engine"] | None = None
    state: NodeRunState
    attempts: int
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    iter: list[int] = Field(default_factory=list)
    loop_path: list[str] = Field(default_factory=list)
    parent_loop_id: str | None = None


class JobDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_slug: str
    workflow_name: str
    workflow_path: str
    state: JobState
    submitted_at: datetime
    updated_at: datetime
    repo_slug: str | None = None
    nodes: list[NodeListEntry] = Field(default_factory=list)
    loop_names: dict[str, str] = Field(default_factory=dict)
    """Loop ``id`` → ``name`` for every loop in the workflow. Loop nodes
    are not emitted as rows; the frontend synthesises section headers
    from ``NodeListEntry.loop_path`` + iter, and uses this map to label
    those headers. Loops without a ``name:`` are absent here; the
    frontend falls back to the loop_id."""


class NodeDetail(BaseModel):
    """Per-node drilldown response."""

    model_config = ConfigDict(extra="forbid")
    node_id: str
    state: NodeRunState
    attempts: int
    last_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    iter: list[int] = Field(default_factory=list)
    """The iter_path this node-detail row corresponds to. Top-level
    nodes get an empty list; loop body executions carry the full path."""
    # Output envelopes the node produced for this iter_path.
    outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)


class HilQueueItem(BaseModel):
    """One pending HIL — either explicit (workflow-declared human-actor
    node) or implicit (Claude calling the ``ask_human`` MCP tool).

    The frontend dispatches on ``kind``: explicit items render
    ``FormRenderer`` (typed schema → widget map); implicit items render
    ``AskHumanDisplay`` (fixed shape: question in, answer out)."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["explicit", "implicit"]
    job_slug: str
    workflow_name: str
    node_id: str
    iter: list[int] = Field(default_factory=list)
    created_at: datetime | None = None

    # Explicit-only (workflow-declared HIL gate).
    output_var_names: list[str] = Field(default_factory=list)
    output_types: dict[str, str] = Field(default_factory=dict)
    presentation: dict[str, Any] = Field(default_factory=dict)
    form_schemas: dict[str, list[tuple[str, str]]] = Field(default_factory=dict)
    """Per-output-var rendered form schema: list of ``(field_name,
    widget_type)`` pairs from the variable type's ``form_schema()``.
    Frontend's ``FormRenderer`` walks the schema and dispatches each
    field through its widget map (``select:opt1,opt2`` → Select, etc.)."""

    # Implicit-only (ask_human MCP call).
    call_id: str | None = None
    question: str | None = None


# ---------------------------------------------------------------------------
# Project projections
# ---------------------------------------------------------------------------


def project_list(root: Path) -> list[ProjectListItem]:
    """Enumerate projects on disk."""
    projects_dir = v1_paths.jobs_dir(root=root).parent / "projects"
    if not projects_dir.is_dir():
        return []
    items: list[ProjectListItem] = []
    for p in sorted(projects_dir.iterdir()):
        item = project_list_item(root, p.name)
        if item is not None:
            items.append(item)
    return items


def project_list_item(root: Path, slug: str) -> ProjectListItem | None:
    pj = root / "projects" / slug / "project.json"
    if not pj.is_file():
        return None
    data = json.loads(pj.read_text())
    open_count = 0
    last_job: datetime | None = None
    for cfg in _iter_jobs(root):
        if cfg.repo_slug == slug or _project_slug_for_repo(data, cfg) == slug:
            if cfg.state in {JobState.SUBMITTED, JobState.RUNNING, JobState.BLOCKED_ON_HUMAN}:
                open_count += 1
            if last_job is None or cfg.updated_at > last_job:
                last_job = cfg.updated_at
    return ProjectListItem(
        slug=slug,
        name=data.get("name", slug),
        repo_path=data.get("repo_path", ""),
        remote_url=data.get("remote_url"),
        default_branch=data.get("default_branch"),
        open_jobs=open_count,
        last_job_at=last_job,
        last_health_check_at=_parse_iso_or_none(data.get("last_health_check_at")),
        last_health_check_status=data.get("last_health_check_status"),
    )


def project_detail(root: Path, slug: str) -> ProjectDetail | None:
    pj = root / "projects" / slug / "project.json"
    if not pj.is_file():
        return None
    data = json.loads(pj.read_text())
    return ProjectDetail(
        slug=slug,
        name=data.get("name", slug),
        repo_path=data.get("repo_path", ""),
        remote_url=data.get("remote_url"),
        default_branch=data.get("default_branch", "main"),
        last_health_check_at=_parse_iso_or_none(data.get("last_health_check_at")),
        last_health_check_status=data.get("last_health_check_status"),
    )


def _project_slug_for_repo(project_data: dict[str, Any], job: JobConfig) -> str | None:
    return None  # repo_slug pinning could be added later; KISS.


# ---------------------------------------------------------------------------
# Job projections
# ---------------------------------------------------------------------------


def _iter_jobs(root: Path) -> list[JobConfig]:
    jobs_dir = v1_paths.jobs_dir(root=root)
    if not jobs_dir.is_dir():
        return []
    out: list[JobConfig] = []
    for jd in sorted(jobs_dir.iterdir()):
        cfg_path = jd / "job.json"
        if not cfg_path.is_file():
            continue
        try:
            out.append(JobConfig.model_validate_json(cfg_path.read_text()))
        except Exception:
            continue
    return out


def job_list(
    root: Path,
    *,
    repo_slug: str | None = None,
    state: JobState | None = None,
) -> list[JobListItem]:
    out: list[JobListItem] = []
    for cfg in _iter_jobs(root):
        if repo_slug is not None and cfg.repo_slug != repo_slug:
            continue
        if state is not None and cfg.state != state:
            continue
        out.append(_to_job_list_item(cfg))
    return out


def _to_job_list_item(cfg: JobConfig) -> JobListItem:
    return JobListItem(
        job_slug=cfg.job_slug,
        workflow_name=cfg.workflow_name,
        state=cfg.state,
        submitted_at=cfg.submitted_at,
        updated_at=cfg.updated_at,
        repo_slug=cfg.repo_slug,
    )


def job_list_item(root: Path, job_slug: str) -> JobListItem | None:
    cfg = _read_job_config(root, job_slug)
    if cfg is None:
        return None
    return _to_job_list_item(cfg)


def job_detail(root: Path, job_slug: str) -> JobDetail | None:
    cfg = _read_job_config(root, job_slug)
    if cfg is None:
        return None
    workflow = _try_load_workflow(cfg.workflow_path)
    return JobDetail(
        job_slug=cfg.job_slug,
        workflow_name=cfg.workflow_name,
        workflow_path=cfg.workflow_path,
        state=cfg.state,
        submitted_at=cfg.submitted_at,
        updated_at=cfg.updated_at,
        repo_slug=cfg.repo_slug,
        nodes=_list_nodes(root, job_slug, workflow),
        loop_names=_collect_loop_names(workflow),
    )


def _collect_loop_names(workflow: Workflow | None) -> dict[str, str]:
    """Walk the DAG (including nested loop bodies) and collect every
    LoopNode's ``id → name`` where ``name`` is set. Loops without a
    name are absent so the frontend falls back to the loop_id."""
    if workflow is None:
        return {}
    names: dict[str, str] = {}

    def visit(nodes: list[Node]) -> None:
        for n in nodes:
            if isinstance(n, LoopNode):
                if n.name:
                    names[n.id] = n.name
                visit(n.body)

    visit(workflow.nodes)
    return names


def _try_load_workflow(workflow_path: str) -> Workflow | None:
    """Best-effort workflow load.

    Returns ``None`` when the file is missing, malformed, or doesn't
    conform to ``Workflow``. Callers fall back to flat node enumeration
    so the dashboard still renders something useful for debugging.
    """
    if not workflow_path:
        return None
    try:
        return load_workflow(Path(workflow_path))
    except WorkflowLoadError as exc:
        log.warning("could not load workflow at %s: %s", workflow_path, exc)
        return None


def _read_job_config(root: Path, job_slug: str) -> JobConfig | None:
    cfg_path = v1_paths.job_config_path(job_slug, root=root)
    if not cfg_path.is_file():
        return None
    try:
        return JobConfig.model_validate_json(cfg_path.read_text())
    except Exception:
        return None


def _list_nodes(root: Path, job_slug: str, workflow: Workflow | None) -> list[NodeListEntry]:
    """Build the unrolled node list for a job.

    With a loadable workflow: walk declaration order; loops are expanded
    into per-iteration body rows tagged with ``iter`` + ``loop_path`` +
    ``parent_loop_id``. Without one: fall back to a flat enumeration of
    every ``nodes/<id>/<iter_token>/state.json`` on disk."""
    if workflow is None:
        return _list_nodes_flat(root, job_slug)
    out: list[NodeListEntry] = []
    for node in workflow.nodes:
        _emit_node_rows(
            node,
            root=root,
            job_slug=job_slug,
            out=out,
            iter_path=(),
            loop_path=[],
            parent_loop_id=None,
        )
    return out


def _list_nodes_flat(root: Path, job_slug: str) -> list[NodeListEntry]:
    """Workflow-less fallback: read every ``nodes/<id>/<token>/state.json``
    we can find on disk. Loop / parent metadata is unavailable here, so
    rows carry only ``node_id`` + ``iter`` + state fields."""
    nodes_dir = v1_paths.nodes_dir(job_slug, root=root)
    if not nodes_dir.is_dir():
        return []
    out: list[NodeListEntry] = []
    for nd in sorted(nodes_dir.iterdir()):
        if not nd.is_dir():
            continue
        for iter_dir in sorted(nd.iterdir()):
            if not iter_dir.is_dir():
                continue
            sp = iter_dir / "state.json"
            if not sp.is_file():
                continue
            try:
                ip = v1_paths.parse_iter_token(iter_dir.name)
            except ValueError:
                continue
            try:
                nr = NodeRun.model_validate_json(sp.read_text())
            except Exception:
                continue
            out.append(
                NodeListEntry(
                    node_id=nr.node_id,
                    state=nr.state,
                    attempts=nr.attempts,
                    last_error=nr.last_error,
                    started_at=nr.started_at,
                    finished_at=nr.finished_at,
                    iter=list(ip),
                )
            )
    return out


def _emit_node_rows(
    node: Node,
    *,
    root: Path,
    job_slug: str,
    out: list[NodeListEntry],
    iter_path: tuple[int, ...],
    loop_path: list[str],
    parent_loop_id: str | None,
) -> None:
    """Append rows for *node*. Loop nodes are not emitted directly — their
    body nodes are emitted per iteration with ``iter_path`` and
    ``loop_path`` extended.

    Always emits at least iter 0 of every loop, even when no envelopes
    or state files have landed on disk yet. That way the operator sees
    the workflow structure upfront; body rows display ``pending`` until
    the engine reaches them."""
    if isinstance(node, LoopNode):
        # Always show at least iter 0 — workflow structure visible to
        # the operator before the engine produces its first envelope.
        # Once iter 0 lands a state/envelope, _count_loop_iterations_seen
        # returns 1; iter N only shows once we observe state at index N.
        iters = max(1, _count_loop_iterations_seen(node, iter_path, job_slug, root))
        for i in range(iters):
            for body in node.body:
                _emit_node_rows(
                    body,
                    root=root,
                    job_slug=job_slug,
                    out=out,
                    iter_path=(*iter_path, i),
                    loop_path=[*loop_path, node.id],
                    parent_loop_id=node.id,
                )
        return
    out.append(
        _build_node_entry(
            node,
            root=root,
            job_slug=job_slug,
            iter_path=iter_path,
            loop_path=loop_path,
            parent_loop_id=parent_loop_id,
        )
    )


def _build_node_entry(
    node: ArtifactNode | CodeNode,
    *,
    root: Path,
    job_slug: str,
    iter_path: tuple[int, ...],
    loop_path: list[str],
    parent_loop_id: str | None,
) -> NodeListEntry:
    """Read on-disk state for *(node, iter_path)* and synthesise the
    per-row entry.

    With v2 path keying, every ``(node_id, iter_path)`` has its own
    ``state.json`` — no need to refine state from envelope existence;
    the state file is per-iter and authoritative."""
    sp = v1_paths.node_state_path(job_slug, node.id, iter_path, root=root)
    nr: NodeRun | None = None
    if sp.is_file():
        try:
            nr = NodeRun.model_validate_json(sp.read_text())
        except Exception:
            nr = None

    state: NodeRunState = nr.state if nr is not None else NodeRunState.PENDING
    attempts = nr.attempts if nr is not None else 0
    last_error = nr.last_error if nr is not None else None
    started_at = nr.started_at if nr is not None else None
    finished_at = nr.finished_at if nr is not None else None

    # Fallback signal: when state.json hasn't been written yet but the
    # node has produced its envelope (e.g. tests seeding only envelopes
    # via FakeEngine.complete_node), surface SUCCEEDED so the row
    # reflects observable progress.
    if nr is None and _node_has_envelope_at(node, iter_path, job_slug, root):
        state = NodeRunState.SUCCEEDED

    return NodeListEntry(
        node_id=node.id,
        name=node.name,
        kind=node.kind,
        actor=node.actor,
        state=state,
        attempts=attempts,
        last_error=last_error,
        started_at=started_at,
        finished_at=finished_at,
        iter=list(iter_path),
        loop_path=list(loop_path),
        parent_loop_id=parent_loop_id,
    )


def _node_has_envelope_at(
    node: ArtifactNode | CodeNode,
    iter_path: tuple[int, ...],
    job_slug: str,
    root: Path,
) -> bool:
    """True iff any of *node*'s declared output variables has an
    envelope at the exact ``iter_path``."""
    for output_ref in (node.outputs or {}).values():
        var_name = output_ref.lstrip("$").split(".", 1)[0].rstrip("?")
        if not var_name:
            continue
        env = v1_paths.variable_envelope_path(job_slug, var_name, iter_path, root=root)
        if env.is_file():
            return True
    return False


def _count_loop_iterations_seen(
    loop: LoopNode,
    enclosing_iter_path: tuple[int, ...],
    job_slug: str,
    root: Path,
) -> int:
    """Highest visible iteration index for *loop* (under the given
    enclosing scope) + 1. Zero if nothing on disk yet.

    Strategy: union evidence from every body node's per-iter state
    directory and per-iter output envelope. We look at body nodes
    recursively (so an inner-loop header iter still surfaces an outer
    iter even when the leaf hasn't completed yet) but only count
    direct-child iter indices (the int at depth ``len(enclosing_iter_path)``).
    """
    depth = len(enclosing_iter_path)
    highest = -1

    # Walk every leaf node in the loop body (recursing through inner
    # loops). For each leaf, scan ``nodes/<id>/`` for iter directories
    # whose iter_path begins with enclosing_iter_path; the index at
    # ``depth`` is this loop's iteration counter.
    for leaf in _iter_leaf_nodes(loop.body):
        node_dir = v1_paths.node_dir(job_slug, leaf.id, root=root)
        if node_dir.is_dir():
            for iter_dir in node_dir.iterdir():
                if not iter_dir.is_dir():
                    continue
                try:
                    ip = v1_paths.parse_iter_token(iter_dir.name)
                except ValueError:
                    continue
                if len(ip) <= depth:
                    continue
                if ip[:depth] != enclosing_iter_path:
                    continue
                if ip[depth] > highest:
                    highest = ip[depth]

        # Also inspect envelope files for body output vars: ``<var>__i<...>.json``.
        for output_ref in (leaf.outputs or {}).values():
            var_name = output_ref.lstrip("$").split(".", 1)[0].rstrip("?")
            if not var_name:
                continue
            var_dir = v1_paths.variables_dir(job_slug, root=root)
            if not var_dir.is_dir():
                continue
            prefix = f"{var_name}__"
            suffix = ".json"
            for p in var_dir.iterdir():
                name = p.name
                if not name.startswith(prefix) or not name.endswith(suffix):
                    continue
                token = name[len(prefix) : -len(suffix)]
                try:
                    ip = v1_paths.parse_iter_token(token)
                except ValueError:
                    continue
                if len(ip) <= depth:
                    continue
                if ip[:depth] != enclosing_iter_path:
                    continue
                if ip[depth] > highest:
                    highest = ip[depth]

    return highest + 1


def _iter_leaf_nodes(nodes: list[Node]) -> list[ArtifactNode | CodeNode]:
    """Flatten a node list to its leaf (artifact / code) nodes,
    recursing through nested LoopNode bodies."""
    out: list[ArtifactNode | CodeNode] = []
    for n in nodes:
        if isinstance(n, LoopNode):
            out.extend(_iter_leaf_nodes(n.body))
        else:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# Node projections
# ---------------------------------------------------------------------------


def node_detail(
    root: Path,
    job_slug: str,
    node_id: str,
    iter_path: tuple[int, ...] = (),
) -> NodeDetail | None:
    """Per-(node, iter_path) drilldown.

    With v2 path keying: ``state.json`` lives at
    ``nodes/<id>/<iter_token>/state.json`` and outputs sit at
    ``variables/<var>__<iter_token>.json``. Filtering is structural — we
    look up each declared output's expected filename directly rather
    than scanning the whole variables/ dir.
    """
    sp = v1_paths.node_state_path(job_slug, node_id, iter_path, root=root)
    if not sp.is_file():
        return None
    try:
        nr = NodeRun.model_validate_json(sp.read_text())
    except Exception:
        return None

    outputs: dict[str, dict[str, Any]] = {}
    cfg = _read_job_config(root, job_slug)
    workflow = _try_load_workflow(cfg.workflow_path) if cfg is not None else None
    var_names = _node_output_var_names(workflow, node_id)

    for var_name in var_names:
        env_path = v1_paths.variable_envelope_path(job_slug, var_name, iter_path, root=root)
        if not env_path.is_file():
            continue
        try:
            text = env_path.read_text()
        except OSError:
            continue
        # Pointer files (``{"$ref": "..."}``) at outer scopes belong to
        # the loop, not the body node — skip them.
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(raw, dict) and set(raw.keys()) == {"$ref"}:
            continue
        try:
            env = Envelope.model_validate(raw)
        except Exception:
            continue
        if env.producer_node != node_id:
            # Different producer → either a loop projection materialised
            # an aggregated value at this slot, or someone else owns it.
            continue
        outputs[env_path.stem] = json.loads(env.model_dump_json())

    return NodeDetail(
        node_id=nr.node_id,
        state=nr.state,
        attempts=nr.attempts,
        last_error=nr.last_error,
        started_at=nr.started_at,
        finished_at=nr.finished_at,
        iter=list(iter_path),
        outputs=outputs,
    )


def _node_output_var_names(workflow: Workflow | None, node_id: str) -> list[str]:
    """Find *node_id* in the workflow (recursing into loop bodies) and
    return its declared output variable names. Empty list if the node
    isn't found — the caller surfaces no outputs in that case rather
    than scanning blindly."""
    if workflow is None:
        return []

    def visit(nodes: list[Node]) -> list[str] | None:
        for n in nodes:
            if isinstance(n, LoopNode):
                hit = visit(n.body)
                if hit is not None:
                    return hit
                continue
            if n.id == node_id:
                names: list[str] = []
                for ref in (n.outputs or {}).values():
                    v = ref.lstrip("$").split(".", 1)[0].rstrip("?")
                    if v:
                        names.append(v)
                return names
        return None

    return visit(workflow.nodes) or []


# ---------------------------------------------------------------------------
# HIL projections
# ---------------------------------------------------------------------------


def hil_queue(root: Path, *, job_slug: str | None = None) -> list[HilQueueItem]:
    """Enumerate every pending HIL — explicit *and* implicit — across
    all jobs (or one job when ``job_slug`` is set)."""
    items: list[HilQueueItem] = []
    if job_slug is not None:
        items.extend(_hil_queue_for_job(root, job_slug))
        return items
    jobs_dir = v1_paths.jobs_dir(root=root)
    if jobs_dir.is_dir():
        for jd in sorted(jobs_dir.iterdir()):
            items.extend(_hil_queue_for_job(root, jd.name))
    return items


def _hil_queue_for_job(root: Path, job_slug: str) -> list[HilQueueItem]:
    workflow_name = _workflow_name_for_job(root, job_slug)
    out: list[HilQueueItem] = []
    out.extend(_hil_explicit_for_job(root, job_slug, workflow_name))
    out.extend(_hil_implicit_for_job(root, job_slug, workflow_name))
    return out


def _workflow_name_for_job(root: Path, job_slug: str) -> str:
    cfg = _read_job_config(root, job_slug)
    return cfg.workflow_name if cfg is not None else job_slug


def _hil_explicit_for_job(root: Path, job_slug: str, workflow_name: str) -> list[HilQueueItem]:
    pending_dir = v1_paths.pending_dir(job_slug, root=root)
    if not pending_dir.is_dir():
        return []
    out: list[HilQueueItem] = []
    for f in sorted(pending_dir.glob("*.json")):
        item = _read_pending_marker(job_slug, workflow_name, f)
        if item is not None:
            out.append(item)
    return out


def _hil_implicit_for_job(root: Path, job_slug: str, workflow_name: str) -> list[HilQueueItem]:
    asks_dir = v1_paths.job_dir(job_slug, root=root) / "asks"
    if not asks_dir.is_dir():
        return []
    out: list[HilQueueItem] = []
    for f in sorted(asks_dir.glob("*.json")):
        item = _read_ask_marker(job_slug, workflow_name, f)
        if item is not None:
            out.append(item)
    return out


def hil_queue_item(root: Path, job_slug: str, node_id: str) -> HilQueueItem | None:
    """Return the pending HIL for ``(job_slug, node_id)`` if any.

    With v2 keying, the marker filename is ``<node_id>__<iter_token>.json``;
    a node may have multiple pending markers across iter_paths (rare —
    usually only one is open at a time). When more than one matches, the
    first sorted match is returned. Callers needing iter disambiguation
    should iterate ``hil_queue(root, job_slug=...)``."""
    pdir = v1_paths.pending_dir(job_slug, root=root)
    if not pdir.is_dir():
        return None
    workflow_name = _workflow_name_for_job(root, job_slug)
    for f in sorted(pdir.glob(f"{node_id}__*.json")):
        item = _read_pending_marker(job_slug, workflow_name, f)
        if item is not None and item.node_id == node_id:
            return item
    return None


def hil_queue_ask(root: Path, job_slug: str, call_id: str) -> HilQueueItem | None:
    """Return the implicit ``ask_human`` marker for ``(job_slug, call_id)``."""
    f = v1_paths.job_dir(job_slug, root=root) / "asks" / f"{call_id}.json"
    if not f.is_file():
        return None
    workflow_name = _workflow_name_for_job(root, job_slug)
    return _read_ask_marker(job_slug, workflow_name, f)


def _read_pending_marker(job_slug: str, workflow_name: str, path: Path) -> HilQueueItem | None:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    created_at = _parse_iso_or_none(data.get("created_at"))
    iter_list = _iter_from_pending(data, path)
    output_types = dict(data.get("output_types") or {})
    return HilQueueItem(
        kind="explicit",
        job_slug=job_slug,
        workflow_name=workflow_name,
        node_id=data.get("node_id", _node_id_from_pending_filename(path.stem)),
        iter=iter_list,
        created_at=created_at,
        output_var_names=list(data.get("output_var_names") or []),
        output_types=output_types,
        presentation=dict(data.get("presentation") or {}),
        form_schemas=_build_form_schemas(output_types),
    )


def _node_id_from_pending_filename(stem: str) -> str:
    """Pending markers are named ``<node_id>__<iter_token>``. Strip the
    iter_token suffix; if none, the whole stem is the node_id (defensive
    against legacy markers)."""
    sep = stem.rfind("__")
    if sep < 0:
        return stem
    return stem[:sep]


def _build_form_schemas(
    output_types: dict[str, str],
) -> dict[str, list[tuple[str, str]]]:
    """For each (var_name, type_name), look up the type and serialise
    its ``form_schema()`` to ``[(field, widget_type), ...]``. Skip types
    whose ``form_schema`` returns ``None`` (not human-producible)."""
    from shared.v1.types.registry import UnknownVariableType, get_type

    out: dict[str, list[tuple[str, str]]] = {}
    for var_name, type_name in output_types.items():
        try:
            t = get_type(type_name)
        except UnknownVariableType:
            continue
        try:
            decl_obj = t.Decl()
            schema = t.form_schema(decl_obj)
        except Exception:
            continue
        if schema is None:
            continue
        fields = list(getattr(schema, "fields", []) or [])
        out[var_name] = [(str(f), str(w)) for f, w in fields]
    return out


def _read_ask_marker(job_slug: str, workflow_name: str, path: Path) -> HilQueueItem | None:
    """Project an implicit-HIL ask marker. Returns ``None`` if the marker
    has already been answered (``answer`` field present) or is malformed."""
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if "answer" in data:
        # Already answered; the engine's MCP server will clean it up.
        return None
    question = data.get("question")
    if not isinstance(question, str):
        return None
    iter_list = _iter_from_ask(data.get("iter"))
    created_at = _parse_iso_or_none(data.get("created_at"))
    return HilQueueItem(
        kind="implicit",
        job_slug=job_slug,
        workflow_name=workflow_name,
        node_id=str(data.get("node_id") or ""),
        iter=iter_list,
        created_at=created_at,
        call_id=path.stem,
        question=question,
    )


def _iter_from_pending(data: dict[str, Any], path: Path) -> list[int]:
    """Decode the pending marker's iter_path. Source of truth is the
    marker's filename suffix (``<node_id>__<iter_token>.json``); the
    in-body ``iter_path`` array is treated as a redundant copy. Falls
    back to body if the filename is malformed."""
    stem = path.stem
    sep = stem.rfind("__")
    if sep >= 0:
        token = stem[sep + 2 :]
        try:
            return list(v1_paths.parse_iter_token(token))
        except ValueError:
            pass
    full = data.get("iter_path")
    if isinstance(full, list) and all(isinstance(x, int) for x in full):
        return list(full)
    return []


def _iter_from_ask(raw: Any) -> list[int]:
    """``HAMMOCK_NODE_ITER`` env value as written by ``ask_human``: comma-
    separated ints, possibly missing. ``"0,1"`` → ``[0, 1]``."""
    if not isinstance(raw, str) or not raw.strip():
        return []
    parts = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            parts.append(int(tok))
        except ValueError:
            return []
    return parts


def _parse_iso_or_none(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None
