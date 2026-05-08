"""Read claude's stream-json transcript for a node's run.

Each agent-actor node writes one JSON object per line to
``<job_dir>/nodes/<node_id>/<iter_token>/runs/<n>/chat.jsonl`` (claude's
``--output-format stream-json``). This module is a pure-function read
of that file, used by the dashboard's chat endpoint.

Old jobs (pre-rename) have ``stdout.log`` instead of ``chat.jsonl``; the
endpoint surfaces those as "no chat transcript" by returning an empty
list — we don't try to parse plain text as turns.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from shared.v1 import paths

log = logging.getLogger(__name__)


def read_agent_chat(
    root: Path,
    job_slug: str,
    node_id: str,
    iter_path: tuple[int, ...] = (),
    attempt: int = 1,
) -> list[dict[str, Any]]:
    """Parse ``chat.jsonl`` into a list of turn dicts.

    Looks up the file at
    ``<job_dir>/nodes/<node_id>/<iter_token>/runs/<attempt>/chat.jsonl``
    via ``paths.node_attempt_dir``. Top-level executions use
    ``iter_path=()``.

    Returns ``[]`` when the file doesn't exist (old jobs, not-yet-run
    nodes, or the node isn't an agent node so no claude was spawned).
    Skips malformed lines with a warning — claude can be killed
    mid-turn and write a partial JSON line.
    """
    chat_path = (
        paths.node_attempt_dir(job_slug, node_id, attempt, iter_path, root=root) / "chat.jsonl"
    )
    if not chat_path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with chat_path.open("r", encoding="utf-8", errors="replace") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                log.warning(
                    "skipping malformed chat.jsonl line %d in %s: %s",
                    lineno,
                    chat_path,
                    exc,
                )
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out
