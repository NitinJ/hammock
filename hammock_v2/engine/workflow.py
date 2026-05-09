"""Hammock v2 workflow loader.

The schema is intentionally tiny:

    name: fix-bug
    description: ...
    nodes:
      - id: write-bug-report
        prompt: write-bug-report
      - id: write-design-spec
        prompt: write-design-spec
        after: [write-bug-report]
      - id: review-design-spec
        prompt: review
        after: [write-design-spec]
        human_review: true
      ...

That is the entire schema. No types, no envelopes, no loops — the
orchestrator agent decides everything else at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class WorkflowError(Exception):
    """Raised when a workflow YAML can't be parsed or fails sanity checks."""


def _default_requires() -> list[str]:
    return ["output.md"]


class Node(BaseModel):
    """A single node in the workflow DAG."""

    model_config = {"extra": "forbid"}

    id: str = Field(..., min_length=1, description="Unique node id.")
    prompt: str = Field(..., min_length=1, description="Prompt template name (without .md).")
    after: list[str] = Field(default_factory=list, description="Node ids that must complete first.")
    human_review: bool = Field(
        default=False,
        description="If true, orchestrator pauses after the agent's first pass and waits for human_decision.md.",
    )
    description: str | None = Field(default=None, description="Optional human-readable note.")
    requires: list[str] = Field(
        default_factory=_default_requires,
        description=(
            "Files (relative to the node's folder) that must exist + be non-empty "
            "before the node is marked succeeded. Strict file-existence check; "
            "no semantic verification. Defaults to ['output.md']."
        ),
    )
    worktree: bool = Field(
        default=False,
        description=(
            "If true, the orchestrator dispatches this node's subagent with "
            "isolation='worktree' so it gets its own git worktree. Use for "
            "code-bearing nodes (implement, open-pr) so concurrent or "
            "back-to-back runs don't collide on the project repo."
        ),
    )

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not v.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"node id {v!r} must be alphanumeric with - or _")
        return v

    @field_validator("requires")
    @classmethod
    def _requires_shape(cls, v: list[str]) -> list[str]:
        for path in v:
            if not path or path.startswith("/") or ".." in path.split("/"):
                raise ValueError(
                    f"requires entry {path!r} must be a non-empty relative path "
                    "without '..' segments"
                )
        return v


class Workflow(BaseModel):
    """A workflow specification."""

    model_config = {"extra": "forbid"}

    name: str = Field(..., min_length=1)
    description: str | None = None
    nodes: list[Node] = Field(..., min_length=1)

    @field_validator("nodes")
    @classmethod
    def _unique_ids(cls, nodes: list[Node]) -> list[Node]:
        seen: set[str] = set()
        for n in nodes:
            if n.id in seen:
                raise ValueError(f"duplicate node id: {n.id!r}")
            seen.add(n.id)
        return nodes


def load_workflow(path: Path) -> Workflow:
    """Load + validate a workflow yaml."""
    if not path.is_file():
        raise WorkflowError(f"workflow file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise WorkflowError(f"workflow {path} is not valid yaml: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise WorkflowError(
            f"workflow {path} top-level must be a mapping, got {type(raw).__name__}"
        )
    try:
        wf = Workflow.model_validate(raw)
    except ValidationError as exc:
        raise WorkflowError(f"workflow {path} schema invalid:\n{exc}") from exc

    # DAG sanity: every `after:` entry must reference a known node, no cycles.
    ids = {n.id for n in wf.nodes}
    for n in wf.nodes:
        for dep in n.after:
            if dep not in ids:
                raise WorkflowError(f"node {n.id!r}: after references unknown node {dep!r}")
    if _has_cycle(wf.nodes):
        raise WorkflowError(f"workflow {path}: cycle detected in after: edges")
    return wf


def topological_order(workflow: Workflow) -> list[Node]:
    """Return nodes in a topo-sorted order respecting `after:` edges."""
    by_id = {n.id: n for n in workflow.nodes}
    visited: set[str] = set()
    order: list[Node] = []

    def visit(node_id: str, stack: list[str]) -> None:
        if node_id in visited:
            return
        if node_id in stack:
            cycle = " → ".join([*stack[stack.index(node_id) :], node_id])
            raise WorkflowError(f"cycle: {cycle}")
        n = by_id[node_id]
        for dep in n.after:
            visit(dep, [*stack, node_id])
        visited.add(node_id)
        order.append(n)

    for n in workflow.nodes:
        visit(n.id, [])
    return order


def _has_cycle(nodes: Sequence[Node]) -> bool:
    by_id = {n.id: n for n in nodes}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(by_id, WHITE)

    def dfs(nid: str) -> bool:
        color[nid] = GRAY
        for dep in by_id[nid].after:
            if color[dep] == GRAY:
                return True
            if color[dep] == WHITE and dfs(dep):
                return True
        color[nid] = BLACK
        return False

    return any(dfs(nid) for nid in by_id if color[nid] == WHITE)


def workflow_summary(workflow: Workflow) -> dict[str, Any]:
    """Cheap projection used by the dashboard for job list / detail."""
    return {
        "name": workflow.name,
        "description": workflow.description,
        "nodes": [
            {
                "id": n.id,
                "prompt": n.prompt,
                "after": list(n.after),
                "human_review": n.human_review,
                "description": n.description,
                "requires": list(n.requires),
                "worktree": n.worktree,
            }
            for n in workflow.nodes
        ],
    }
