"""Path classification for v1 disk layout.

Per design-patch §1.7 and the v1 layout helpers in ``shared/v1/paths``.
The watcher uses ``classify_path`` to map filesystem events to a
semantic ``kind``; the SSE handler uses ``scopes_for`` to fan changes
out to the right subscribers.

This module replaces the v0 path classification that lived in
``dashboard.state.cache``. It targets the v1 layout exclusively.

v2 keying (loops-v2): every execution is identified by
``(node_id, iter_path)``. State and envelope filenames carry the iter
token explicitly:

  - ``nodes/<node_id>/<iter_token>/state.json``
  - ``variables/<var>__<iter_token>.json``
  - ``pending/<node_id>__<iter_token>.json``
  - ``nodes/<node_id>/<iter_token>/runs/<n>/chat.jsonl``  (chat tail)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

from shared.v1 import paths as v1_paths

PathKind = Literal[
    "project",  # projects/<slug>/project.json
    "job",  # jobs/<slug>/job.json
    "node",  # jobs/<slug>/nodes/<node_id>/<iter_token>/state.json
    "variable",  # jobs/<slug>/variables/<var>__<iter_token>.json
    "pending",  # jobs/<slug>/pending/<node_id>__<iter_token>.json
    "ask",  # jobs/<slug>/asks/<call_id>.json (implicit HIL marker)
    "events_jsonl",  # jobs/<slug>/events.jsonl
    "chat_jsonl",  # jobs/<slug>/nodes/<id>/<token>/runs/<n>/chat.jsonl
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
    iter_path: tuple[int, ...] | None = None
    call_id: str | None = None
    attempt: int | None = None


class ChangeKind(StrEnum):
    """Mutation type for a filesystem change."""

    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


def classify_path(path: Path, root: Path) -> ClassifiedPath:
    """Map an absolute *path* under *root* to its semantic kind.

    Returns ``ClassifiedPath(kind="unknown")`` for paths the dashboard
    does not track (per-attempt prompts / stderr / output.json, side
    files).
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

    # jobs/<slug>/nodes/<node_id>/<iter_token>/state.json
    if len(parts) == 6 and parts[0] == "jobs" and parts[2] == "nodes" and parts[5] == "state.json":
        try:
            ip = v1_paths.parse_iter_token(parts[4])
        except ValueError:
            return ClassifiedPath("unknown")
        return ClassifiedPath(
            "node",
            job_slug=parts[1],
            node_id=parts[3],
            iter_path=ip,
        )

    # jobs/<slug>/nodes/<node_id>/<iter_token>/runs/<attempt>/chat.jsonl
    if (
        len(parts) == 8
        and parts[0] == "jobs"
        and parts[2] == "nodes"
        and parts[5] == "runs"
        and parts[7] == "chat.jsonl"
    ):
        try:
            ip = v1_paths.parse_iter_token(parts[4])
        except ValueError:
            return ClassifiedPath("unknown")
        try:
            attempt = int(parts[6])
        except ValueError:
            return ClassifiedPath("unknown")
        return ClassifiedPath(
            "chat_jsonl",
            job_slug=parts[1],
            node_id=parts[3],
            iter_path=ip,
            attempt=attempt,
        )

    # jobs/<slug>/variables/<var>__<iter_token>.json
    if len(parts) == 4 and parts[0] == "jobs" and parts[2] == "variables":
        fname = parts[3]
        if not fname.endswith(".json"):
            return ClassifiedPath("unknown")
        stem = fname[: -len(".json")]
        sep = stem.rfind("__")
        if sep < 0:
            return ClassifiedPath("unknown")
        var_name = stem[:sep]
        token = stem[sep + 2 :]
        try:
            ip = v1_paths.parse_iter_token(token)
        except ValueError:
            return ClassifiedPath("unknown")
        if not var_name:
            return ClassifiedPath("unknown")
        return ClassifiedPath(
            "variable",
            job_slug=parts[1],
            var_name=var_name,
            iter_path=ip,
        )

    # jobs/<slug>/pending/<node_id>__<iter_token>.json
    if (
        len(parts) == 4
        and parts[0] == "jobs"
        and parts[2] == "pending"
        and parts[3].endswith(".json")
    ):
        stem = parts[3][: -len(".json")]
        sep = stem.rfind("__")
        if sep < 0:
            # Defensive: legacy markers without iter token. Treat as
            # top-level (iter_path=()) so they still classify rather
            # than disappear from the watcher's view.
            return ClassifiedPath(
                "pending",
                job_slug=parts[1],
                node_id=stem,
                iter_path=(),
            )
        node_id = stem[:sep]
        token = stem[sep + 2 :]
        try:
            ip = v1_paths.parse_iter_token(token)
        except ValueError:
            return ClassifiedPath("unknown")
        return ClassifiedPath(
            "pending",
            job_slug=parts[1],
            node_id=node_id,
            iter_path=ip,
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
