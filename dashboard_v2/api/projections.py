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
    if nodes_dir.is_dir():
        # Issue: previously sorted alphabetically by folder name, which
        # rendered nodes in the wrong DAG order in the UI. Resolve via
        # the workflow snapshot's topological order; missing folders
        # for not-yet-dispatched nodes still surface as pending.
        ordered_ids = _ordered_node_ids_from_workflow(slug, root)
        on_disk = {p.name: p for p in nodes_dir.iterdir() if p.is_dir()}
        # Topo-ordered first, then any stragglers on disk (defensive).
        seen: set[str] = set()
        ordered_paths: list[Path] = []
        for nid in ordered_ids:
            seen.add(nid)
            # Even if no folder yet, surface a pending placeholder so
            # the timeline shows the full workflow shape from t=0.
            ordered_paths.append(on_disk.get(nid) or (nodes_dir / nid))
        for nid, p in sorted(on_disk.items()):
            if nid not in seen:
                ordered_paths.append(p)
        for node_dir_path in ordered_paths:
            state_path = node_dir_path / "state.md"
            state_front, _ = parse_frontmatter(_safe_read(state_path))
            awaiting = (node_dir_path / "awaiting_human.md").is_file()
            decision = (node_dir_path / "human_decision.md").is_file()
            nodes_overview.append(
                {
                    "id": node_dir_path.name,
                    "state": state_front.get("state", "pending"),
                    "started_at": state_front.get("started_at"),
                    "finished_at": state_front.get("finished_at"),
                    "awaiting_human": awaiting and not decision,
                }
            )
    return {
        "slug": slug,
        "workflow_name": workflow_name,
        "state": front.get("state", "submitted"),
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


def node_detail(slug: str, node_id: str, root: Path) -> dict[str, Any] | None:
    node_dir_path = paths.node_dir(slug, node_id, root=root)
    if not node_dir_path.is_dir():
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
    return parse_jsonl(paths.node_chat_jsonl(slug, node_id, root=root))


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


def orchestrator_events(slug: str, root: Path) -> list[dict[str, Any]]:
    """Build a chronological events list for the orchestrator pseudo-node.

    Sources:
    - job.md (state transitions)
    - nodes/<id>/state.md (per-node started_at / finished_at)
    - awaiting_human.md / human_decision.md presence
    - validation.md attempts

    The orchestrator's own chat.jsonl is rendered separately; this is
    the human-readable timeline.
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
