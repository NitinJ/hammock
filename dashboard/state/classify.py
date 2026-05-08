"""Path classification for v1 disk layout.

Per design-patch §1.7 and the v1 layout helpers in ``shared/v1/paths``.
The watcher uses ``classify_path`` to map filesystem events to a
semantic ``kind``; the SSE handler uses ``scopes_for`` to fan changes
out to the right subscribers.

This module replaces the v0 path classification that lived in
``dashboard.state.cache``. It targets the v1 layout exclusively
(no backwards compatibility with v0 stage / hil paths).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

PathKind = Literal[
    "project",  # projects/<slug>/project.json
    "job",  # jobs/<slug>/job.json
    "node",  # jobs/<slug>/nodes/<node_id>/state.json
    "variable",  # jobs/<slug>/variables/<var>.json (top-level envelope)
    "loop_variable",  # jobs/<slug>/variables/loop_<lid>_<var>_<i>.json
    "pending",  # jobs/<slug>/pending/<node_id>.json (HIL marker)
    "ask",  # jobs/<slug>/asks/<call_id>.json (implicit HIL marker)
    "events_jsonl",  # jobs/<slug>/events.jsonl
    "unknown",
]


@dataclass(frozen=True)
class ClassifiedPath:
    """Decoded meaning of an absolute path under the hammock root."""

    kind: PathKind
    project_slug: str | None = None
    job_slug: str | None = None
    node_id: str | None = None
    var_name: str | None = None
    loop_id: str | None = None
    iteration: int | None = None
    call_id: str | None = None


class ChangeKind(StrEnum):
    """Mutation type for a filesystem change."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


def classify_path(path: Path, root: Path) -> ClassifiedPath:
    """Map an absolute *path* under *root* to its semantic kind.

    Returns ``ClassifiedPath(kind="unknown")`` for paths the dashboard
    does not track (per-attempt run dirs, raw artifacts, side files).
    """
    try:
        rel = path.relative_to(root)
    except ValueError:
        return ClassifiedPath("unknown")

    parts = rel.parts

    # projects/<slug>/project.json
    if len(parts) == 3 and parts[0] == "projects" and parts[2] == "project.json":
        return ClassifiedPath("project", project_slug=parts[1])

    # jobs/<slug>/job.json
    if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "job.json":
        return ClassifiedPath("job", job_slug=parts[1])

    # jobs/<slug>/nodes/<node_id>/state.json
    if len(parts) == 5 and parts[0] == "jobs" and parts[2] == "nodes" and parts[4] == "state.json":
        return ClassifiedPath("node", job_slug=parts[1], node_id=parts[3])

    # jobs/<slug>/variables/<file>
    if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "variables":
        fname = parts[3]
        if not fname.endswith(".json"):
            return ClassifiedPath("unknown")
        stem = fname[: -len(".json")]
        if stem.startswith("loop_"):
            # loop_<loop_id>_<var>_<iteration>
            # Iteration is the last underscore-separated chunk; var is the
            # one before. Loop id is everything between "loop_" and the
            # second-to-last underscore. (Loop ids may contain underscores
            # via the v1.0 path-safety replacement.)
            inner = stem[len("loop_") :]
            try:
                head, iter_str = inner.rsplit("_", 1)
                loop_id, var_name = head.rsplit("_", 1)
                iteration = int(iter_str)
            except (ValueError, AttributeError):
                return ClassifiedPath("unknown")
            return ClassifiedPath(
                "loop_variable",
                job_slug=parts[1],
                var_name=var_name,
                loop_id=loop_id,
                iteration=iteration,
            )
        return ClassifiedPath("variable", job_slug=parts[1], var_name=stem)

    # jobs/<slug>/pending/<node_id>.json
    if (
        len(parts) == 4
        and parts[0] == "jobs"
        and parts[2] == "pending"
        and parts[3].endswith(".json")
    ):
        return ClassifiedPath(
            "pending",
            job_slug=parts[1],
            node_id=parts[3][: -len(".json")],
        )

    # jobs/<slug>/asks/<call_id>.json (implicit HIL marker; the per-job
    # MCP server creates these; the dashboard mutates them in place to
    # answer; the server reads the answer back to its agent caller).
    if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "asks" and parts[3].endswith(".json"):
        return ClassifiedPath(
            "ask",
            job_slug=parts[1],
            call_id=parts[3][: -len(".json")],
        )

    # jobs/<slug>/events.jsonl
    if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "events.jsonl":
        return ClassifiedPath("events_jsonl", job_slug=parts[1])

    return ClassifiedPath("unknown")


def scopes_for(classified: ClassifiedPath) -> list[str]:
    """Return the SSE scope names a subscriber would care about for this
    classified path. Used by the watcher to fan changes out.

    Scopes:
      - ``"global"``                     — every subscriber
      - ``f"project:{slug}"``            — that project's subscribers
      - ``f"job:{slug}"``                — that job's subscribers
      - ``f"node:{job}/{node_id}"``      — drilldown on one node
    """
    scopes: list[str] = ["global"]
    if classified.project_slug:
        scopes.append(f"project:{classified.project_slug}")
    if classified.job_slug:
        scopes.append(f"job:{classified.job_slug}")
    if classified.job_slug and classified.node_id:
        scopes.append(f"node:{classified.job_slug}/{classified.node_id}")
    return scopes
