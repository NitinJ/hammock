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


def job_summary(slug: str, root: Path) -> dict[str, Any] | None:
    job_md = paths.job_md(slug, root=root)
    if not job_md.is_file():
        return None
    front, body = parse_frontmatter(job_md.read_text())
    workflow_name = front.get("workflow", "unknown")
    nodes_overview: list[dict[str, Any]] = []
    nodes_dir = paths.nodes_dir(slug, root=root)
    if nodes_dir.is_dir():
        for node_dir_path in sorted(nodes_dir.iterdir()):
            if not node_dir_path.is_dir():
                continue
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


def load_workflow_or_none(name: str, root: Path | None = None) -> Workflow | None:
    try:
        path = workflow_yaml_path_for_name(name, root=root)
        if path is None:
            return None
        return load_workflow(path)
    except WorkflowError:
        return None


def list_user_workflow_paths(root: Path) -> list[Path]:
    user_dir = root / "workflows"
    if not user_dir.is_dir():
        return []
    return sorted(user_dir.glob("*.yaml"))
