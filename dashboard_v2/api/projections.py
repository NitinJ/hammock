"""Read-side projections for the v2 dashboard.

Everything is markdown on disk; we parse the lightweight YAML
frontmatter for state and surface the rest as plain text.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Any

import yaml

from hammock_v2.engine import paths
from hammock_v2.engine.runner import discover_workflows
from hammock_v2.engine.workflow import (
    Workflow,
    WorkflowError,
    load_workflow,
    workflow_summary,
)

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body_text). Body is empty when no
    frontmatter delimiter is present."""
    if not text:
        return {}, ""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        front = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(front, dict):
        return {}, text
    body = m.group(2)
    return front, body


def _safe_read(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        return ""


def _read_orchestrator_state(slug: str, root: Path) -> dict[str, Any]:
    """Best-effort read of orchestrator_state.json. Returns empty dict
    if missing/unparseable so callers can default cleanly."""
    state_path = paths.orchestrator_state_json(slug, root=root)
    if not state_path.is_file():
        return {}
    try:
        parsed = json.loads(state_path.read_text() or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def expanded_nodes_for(slug: str, root: Path) -> dict[str, dict[str, Any]]:
    """Return the orchestrator's `expanded_nodes` map (prefixed_id →
    metadata including parent_expander). Empty dict when none."""
    state = _read_orchestrator_state(slug, root)
    expanded = state.get("expanded_nodes") or {}
    if not isinstance(expanded, dict):
        return {}
    # Defensive: normalize to dict[str, dict]
    out: dict[str, dict[str, Any]] = {}
    for k, v in expanded.items():
        if isinstance(k, str) and isinstance(v, dict):
            out[k] = v
    return out


def _topo_order_expanded_children(
    child_ids: list[str], expanded_map: dict[str, dict[str, Any]]
) -> list[str]:
    """Topo-order a set of expanded child ids by their `after:` edges.

    Children with no after-edges first, then everything that depends on
    them. All `after:` references are within the same expansion (already
    prefixed). Falls back to insertion order on cycle (defensive — the
    orchestrator validator rejects cycles, but be safe).
    """
    in_set = set(child_ids)
    indeg: dict[str, int] = {cid: 0 for cid in child_ids}
    deps: dict[str, list[str]] = {cid: [] for cid in child_ids}
    for cid in child_ids:
        meta = expanded_map.get(cid, {})
        after = meta.get("after") or []
        if not isinstance(after, list):
            continue
        for dep in after:
            if isinstance(dep, str) and dep in in_set:
                indeg[cid] += 1
                deps.setdefault(dep, []).append(cid)
    out: list[str] = []
    ready = [cid for cid in child_ids if indeg[cid] == 0]
    while ready:
        # Stable order: by original insertion within `child_ids`.
        ready.sort(key=lambda c: child_ids.index(c))
        cur = ready.pop(0)
        out.append(cur)
        for child in deps.get(cur, []):
            indeg[child] -= 1
            if indeg[child] == 0:
                ready.append(child)
    if len(out) != len(child_ids):
        # Cycle (shouldn't happen — validator rejects). Fall back.
        leftover = [c for c in child_ids if c not in out]
        out.extend(leftover)
    return out


def _kind_for_static_node(slug: str, root: Path, node_id: str) -> str | None:
    """Look up a static workflow node's `kind` from the workflow snapshot.

    Returns "agent" / "workflow_expander" or None if not found / on
    parse failure. Cheap on the happy path: load_workflow caches via
    pydantic's own mechanisms; we only call this for top-level nodes.
    """
    snapshot = paths.workflow_yaml(slug, root=root)
    if not snapshot.is_file():
        return None
    try:
        from hammock_v2.engine.workflow import load_workflow

        wf = load_workflow(snapshot)
        for n in wf.nodes:
            if n.id == node_id:
                return n.kind
        return None
    except Exception:
        return None


def _ordered_node_ids_from_workflow(slug: str, root: Path) -> list[str]:
    """Topo-ordered node ids from the job's workflow.yaml snapshot.

    Returns [] if the snapshot is missing or unparseable; caller falls
    back to filesystem-alphabetical order for resilience. The job's
    snapshot lives at <job_dir>/workflow.yaml — durable for the lifetime
    of the job, so this works across page refreshes and after the job
    completes.
    """
    snapshot = paths.workflow_yaml(slug, root=root)
    if not snapshot.is_file():
        return []
    try:
        from hammock_v2.engine.workflow import load_workflow, topological_order

        wf = load_workflow(snapshot)
        return [n.id for n in topological_order(wf)]
    except Exception:
        return []


def job_summary(slug: str, root: Path) -> dict[str, Any] | None:
    job_md = paths.job_md(slug, root=root)
    if not job_md.is_file():
        return None
    front, body = parse_frontmatter(job_md.read_text())
    workflow_name = front.get("workflow", "unknown")
    nodes_overview: list[dict[str, Any]] = []
    nodes_dir = paths.nodes_dir(slug, root=root)
    expanded_map = expanded_nodes_for(slug, root)
    # Group expanded children by parent_expander so we can interleave
    # them right after their parent in the timeline order.
    expanded_by_parent: dict[str, list[str]] = {}
    for prefixed_id, meta in expanded_map.items():
        parent = meta.get("parent_expander")
        if isinstance(parent, str):
            expanded_by_parent.setdefault(parent, []).append(prefixed_id)
    if nodes_dir.is_dir():
        # Issue: previously sorted alphabetically by folder name, which
        # rendered nodes in the wrong DAG order in the UI. Resolve via
        # the workflow snapshot's topological order; missing folders
        # for not-yet-dispatched nodes still surface as pending.
        ordered_ids = _ordered_node_ids_from_workflow(slug, root)
        on_disk = {p.name: p for p in nodes_dir.iterdir() if p.is_dir()}
        # Topo-ordered first, then any stragglers on disk (defensive).
        seen: set[str] = set()
        ordered_paths: list[tuple[Path, str | None]] = []  # (path, parent_expander)
        for nid in ordered_ids:
            seen.add(nid)
            # Even if no folder yet, surface a pending placeholder so
            # the timeline shows the full workflow shape from t=0.
            ordered_paths.append((on_disk.get(nid) or (nodes_dir / nid), None))
            # Right after the parent expander, append its expanded children
            # in their topo order (children with empty after: first, then
            # by dependency depth).
            if nid in expanded_by_parent:
                child_ids = _topo_order_expanded_children(expanded_by_parent[nid], expanded_map)
                for child_id in child_ids:
                    seen.add(child_id)
                    child_path = nodes_dir / nid / child_id.split("__", 1)[1]
                    ordered_paths.append((child_path, nid))
        for nid, p in sorted(on_disk.items()):
            if nid not in seen:
                ordered_paths.append((p, None))
        for node_dir_path, parent_expander in ordered_paths:
            state_path = node_dir_path / "state.md"
            state_front, _ = parse_frontmatter(_safe_read(state_path))
            awaiting = (node_dir_path / "awaiting_human.md").is_file()
            decision = (node_dir_path / "human_decision.md").is_file()
            # Display id: top-level uses folder name; expanded children
            # use the prefixed runtime id so the frontend can group.
            display_id = node_dir_path.name
            if parent_expander is not None:
                display_id = f"{parent_expander}__{node_dir_path.name}"
            entry: dict[str, Any] = {
                "id": display_id,
                "state": state_front.get("state", "pending"),
                "started_at": state_front.get("started_at"),
                "finished_at": state_front.get("finished_at"),
                "awaiting_human": awaiting and not decision,
                "parent_expander": parent_expander,
            }
            # Mark the static workflow node's kind so the frontend can
            # render an "expander" badge when applicable.
            if parent_expander is None:
                kind = _kind_for_static_node(slug, root, display_id)
                if kind:
                    entry["kind"] = kind
            else:
                entry["kind"] = "agent"
            nodes_overview.append(entry)
    # Lifecycle control gate (operator pause/resume/stop). Disk-derived
    # so it survives refresh; orchestrator polls the same file.
    control_path = paths.control_md(slug, root=root)
    controlled_state = "running"
    if control_path.is_file():
        ctrl_front, _ = parse_frontmatter(control_path.read_text())
        controlled_state = ctrl_front.get("state", "running")
    return {
        "slug": slug,
        "workflow_name": workflow_name,
        "state": front.get("state", "submitted"),
        "controlled_state": controlled_state,
        "submitted_at": front.get("submitted_at"),
        "started_at": front.get("started_at"),
        "finished_at": front.get("finished_at"),
        "error": front.get("error"),
        "request": body.split("## Request", 1)[-1].strip()
        if "## Request" in body
        else body.strip(),
        "nodes": nodes_overview,
    }


def list_jobs(root: Path) -> list[dict[str, Any]]:
    jobs_dir = paths.jobs_dir(root)
    out: list[dict[str, Any]] = []
    if not jobs_dir.is_dir():
        return out
    for entry in sorted(jobs_dir.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        summary = job_summary(entry.name, root=root)
        if summary is not None:
            out.append(summary)
    return out


def resolve_node_dir(slug: str, node_id: str, root: Path) -> Path | None:
    """Resolve a node id (top-level or expanded) to its folder.

    Top-level: <job_dir>/nodes/<node_id>/
    Expanded:  <job_dir>/nodes/<parent_expander>/<child_id>/

    The expanded prefix convention is `<parent_expander>__<child_id>`.
    Returns None if the folder doesn't exist.
    """
    direct = paths.node_dir(slug, node_id, root=root)
    if direct.is_dir():
        return direct
    if "__" in node_id:
        parent, _, child = node_id.partition("__")
        nested = paths.nodes_dir(slug, root=root) / parent / child
        if nested.is_dir():
            return nested
    return None


def node_detail(slug: str, node_id: str, root: Path) -> dict[str, Any] | None:
    node_dir_path = resolve_node_dir(slug, node_id, root)
    if node_dir_path is None:
        return None
    state_front, _ = parse_frontmatter(_safe_read(node_dir_path / "state.md"))
    awaiting = (node_dir_path / "awaiting_human.md").is_file()
    decision_path = node_dir_path / "human_decision.md"
    decision: dict[str, Any] | None = None
    if decision_path.is_file():
        front, body = parse_frontmatter(decision_path.read_text())
        decision = {"decision": front.get("decision"), "comment": body.strip() or None}
    return {
        "id": node_id,
        "state": state_front.get("state", "pending"),
        "started_at": state_front.get("started_at"),
        "finished_at": state_front.get("finished_at"),
        "input": _safe_read(node_dir_path / "input.md"),
        "prompt": _safe_read(node_dir_path / "prompt.md"),
        "output": _safe_read(node_dir_path / "output.md"),
        "awaiting_human": awaiting and decision is None,
        "human_decision": decision,
    }


def parse_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def node_chat(slug: str, node_id: str, root: Path) -> list[dict[str, Any]]:
    # Resolve through the expanded-node-aware lookup so
    # `<parent_expander>__<child_id>` ids find their nested chat.jsonl.
    folder = resolve_node_dir(slug, node_id, root)
    if folder is None:
        return []
    return parse_jsonl(folder / "chat.jsonl")


def orchestrator_chat(slug: str, root: Path) -> list[dict[str, Any]]:
    return parse_jsonl(paths.orchestrator_jsonl(slug, root=root))


def orchestrator_messages(slug: str, root: Path) -> list[dict[str, Any]]:
    """Read the operator <-> orchestrator message queue."""
    return parse_jsonl(paths.orchestrator_messages_jsonl(slug, root=root))


def append_orchestrator_message(
    *,
    slug: str,
    text: str,
    sender: str,
    root: Path,
) -> dict[str, Any]:
    """Append an operator (or orchestrator) message to the message queue."""
    if sender not in ("operator", "orchestrator"):
        raise ValueError(f"sender must be 'operator' or 'orchestrator', got {sender!r}")
    if not text.strip():
        raise ValueError("text cannot be empty")
    target = paths.orchestrator_messages_jsonl(slug, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = parse_jsonl(target)
    next_id = f"msg-{len(existing) + 1}"
    payload = {
        "id": next_id,
        "from": sender,
        "timestamp": _dt.datetime.now(_dt.UTC).isoformat(),
        "text": text,
    }
    with target.open("a") as fh:
        fh.write(json.dumps(payload) + "\n")
    return payload


def _chat_derived_events(slug: str, root: Path) -> list[dict[str, Any]]:
    """Extract durable events from the orchestrator's own stream-json
    transcript.

    Two event kinds emerge here:
    - `subagent_dispatched`: an `assistant` turn issued a `tool_use`
      with `name: "Task"`. The Task's `description` typically includes
      the node id (e.g. "Run write-bug-report").
    - `subagent_completed`: a `user` turn carries a matching
      `tool_use_result`.

    Both come from `orchestrator.jsonl` which the runner persists for
    the lifetime of the job, so a page refresh recomputes the same
    events from the same file.
    """
    out: list[dict[str, Any]] = []
    transcript_path = paths.orchestrator_jsonl(slug, root=root)
    if not transcript_path.is_file():
        return out
    pending: dict[str, str] = {}  # tool_use_id -> node_id
    for entry in parse_jsonl(transcript_path):
        ts = entry.get("timestamp") or ""
        msg = entry.get("message") or {}
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if entry.get("type") == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                if block.get("name") != "Task":
                    continue
                tool_use_id = block.get("id") or ""
                inp = block.get("input") or {}
                desc = inp.get("description") if isinstance(inp, dict) else None
                node_id = _node_id_from_description(desc)
                pending[tool_use_id] = node_id or ""
                out.append(
                    {
                        "kind": "subagent_dispatched",
                        "at": ts,
                        "node_id": node_id,
                        "detail": f"orchestrator dispatched Task: {desc or '(no description)'}",
                    }
                )
        elif entry.get("type") == "user":
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue
                tool_use_id = block.get("tool_use_id") or ""
                node_id = pending.pop(tool_use_id, "") or None
                detail = "subagent returned"
                if node_id:
                    detail = f"subagent {node_id} returned"
                out.append(
                    {
                        "kind": "subagent_completed",
                        "at": ts,
                        "node_id": node_id,
                        "detail": detail,
                    }
                )
    return out


def _node_id_from_description(desc: object) -> str | None:
    """Best-effort: pull a node id out of a Task description string.

    Conventional shape we instruct the orchestrator to emit:
    `"Run <node-id>"`. Fall back to the whole string trimmed.
    """
    if not isinstance(desc, str):
        return None
    s = desc.strip()
    if s.lower().startswith("run "):
        candidate = s[4:].strip()
        return candidate or None
    return None


def orchestrator_events(slug: str, root: Path) -> list[dict[str, Any]]:
    """Build a chronological events list for the orchestrator pseudo-node.

    Sources (all durable on disk — refresh re-derives the same list):
    - job.md (state transitions)
    - nodes/<id>/state.md (per-node started_at / finished_at)
    - awaiting_human.md / human_decision.md presence
    - validation.md attempts
    - orchestrator.jsonl Task tool_use / tool_result pairs (via
      ``_chat_derived_events``)

    The orchestrator's own chat.jsonl is rendered separately on the
    "Log" tab; this is the human-readable timeline.
    """
    events: list[dict[str, Any]] = []
    job_md_path = paths.job_md(slug, root=root)
    if job_md_path.is_file():
        front, _ = parse_frontmatter(job_md_path.read_text())
        if front.get("submitted_at"):
            events.append(
                {
                    "kind": "job_submitted",
                    "at": front["submitted_at"],
                    "detail": "job submitted",
                }
            )
        if front.get("started_at"):
            events.append(
                {
                    "kind": "job_started",
                    "at": front["started_at"],
                    "detail": "orchestrator started",
                }
            )
        if front.get("finished_at"):
            events.append(
                {
                    "kind": f"job_{front.get('state', 'finished')}",
                    "at": front["finished_at"],
                    "detail": f"job state: {front.get('state', 'finished')}",
                }
            )
    nodes_dir = paths.nodes_dir(slug, root=root)
    if nodes_dir.is_dir():
        for node_dir_path in sorted(nodes_dir.iterdir()):
            if not node_dir_path.is_dir():
                continue
            nid = node_dir_path.name
            state_front, _ = parse_frontmatter(_safe_read(node_dir_path / "state.md"))
            if state_front.get("started_at"):
                events.append(
                    {
                        "kind": "node_started",
                        "at": state_front["started_at"],
                        "node_id": nid,
                        "detail": f"node {nid} started",
                    }
                )
            if state_front.get("finished_at"):
                events.append(
                    {
                        "kind": f"node_{state_front.get('state', 'finished')}",
                        "at": state_front["finished_at"],
                        "node_id": nid,
                        "detail": f"node {nid} {state_front.get('state', 'finished')}",
                    }
                )
            awaiting_path = node_dir_path / "awaiting_human.md"
            if awaiting_path.is_file():
                a_front, _ = parse_frontmatter(awaiting_path.read_text())
                if a_front.get("awaiting_human_since"):
                    events.append(
                        {
                            "kind": "awaiting_human",
                            "at": a_front["awaiting_human_since"],
                            "node_id": nid,
                            "detail": f"node {nid} awaiting human review",
                        }
                    )
            decision_path = node_dir_path / "human_decision.md"
            if decision_path.is_file():
                d_front, body = parse_frontmatter(decision_path.read_text())
                events.append(
                    {
                        "kind": "human_decision",
                        "at": d_front.get("decided_at") or "",
                        "node_id": nid,
                        "detail": (
                            f"human {d_front.get('decision', 'decided')}"
                            + (f": {body.strip()[:120]}" if body.strip() else "")
                        ),
                    }
                )
            validation_path = node_dir_path / "validation.md"
            if validation_path.is_file():
                v_front, body = parse_frontmatter(validation_path.read_text())
                events.append(
                    {
                        "kind": "validation_failure",
                        "at": v_front.get("checked_at") or "",
                        "node_id": nid,
                        "detail": f"validation failed (attempt {v_front.get('attempt', '?')})",
                    }
                )
    # Merge in chat-derived events (Task dispatches + completions).
    events.extend(_chat_derived_events(slug, root))
    # Coerce all timestamps to strings (YAML parses ISO timestamps as
    # datetime objects, which break str-comparison in sort) and ensure
    # JSON-serializable shape.
    for ev in events:
        at = ev.get("at")
        ev["at"] = "" if at is None else str(at)
    # Stable chronological ordering — empty timestamps sort first.
    events.sort(key=lambda e: e.get("at") or "")
    return events


def list_workflows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for wf in discover_workflows():
        out.append(workflow_summary(wf))
    return out


def workflow_detail(name: str) -> dict[str, Any] | None:
    for wf in discover_workflows():
        if wf.name == name:
            return workflow_summary(wf)
    return None


def write_human_decision(
    *,
    slug: str,
    node_id: str,
    decision: str,
    comment: str | None,
    root: Path,
) -> Path:
    """Atomically write human_decision.md so the orchestrator can pick it up."""
    if decision not in {"approved", "needs-revision"}:
        raise ValueError(f"decision must be 'approved' or 'needs-revision', got {decision!r}")
    target = paths.node_human_decision(slug, node_id, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    body_lines = [
        "---",
        f"decision: {decision}",
        f"submitted_at: {_dt.datetime.now(_dt.UTC).isoformat()}",
        "---",
        "",
    ]
    if comment:
        body_lines.append(comment.strip())
    target.write_text("\n".join(body_lines) + "\n")
    return target


def workflow_yaml_path_for_name(name: str, root: Path | None = None) -> Path | None:
    """Resolve a workflow name to a yaml path.

    User-defined workflows under ``<root>/workflows/`` win over bundled
    ones with the same name. ``root`` defaults to the configured
    HAMMOCK_V2_ROOT.
    """
    from hammock_v2.engine.runner import WORKFLOWS_DIR

    if root is not None:
        user_path = root / "workflows" / f"{name}.yaml"
        if user_path.is_file():
            return user_path
    bundled = WORKFLOWS_DIR / f"{name}.yaml"
    if bundled.is_file():
        return bundled
    return None


def load_workflow_or_none(
    name: str,
    root: Path | None = None,
    *,
    override_path: Path | None = None,
) -> Workflow | None:
    try:
        path = (
            override_path
            if override_path is not None
            else workflow_yaml_path_for_name(name, root=root)
        )
        if path is None:
            return None
        return load_workflow(path)
    except WorkflowError:
        return None


def resolve_workflow_path(
    name: str,
    root: Path | None = None,
    *,
    project_repo_path: Path | None = None,
) -> Path | None:
    """Resolve the on-disk path for a workflow, preferring per-project."""
    if project_repo_path is not None:
        candidate = project_repo_path / ".hammock-v2" / "workflows" / f"{name}.yaml"
        if candidate.is_file():
            return candidate
    return workflow_yaml_path_for_name(name, root=root)


def list_user_workflow_paths(root: Path) -> list[Path]:
    user_dir = root / "workflows"
    if not user_dir.is_dir():
        return []
    return sorted(user_dir.glob("*.yaml"))
