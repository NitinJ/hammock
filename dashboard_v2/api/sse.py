"""SSE: watch the job dir for file changes, emit typed events.

Wire format: text/event-stream with named events. Event types:

- event: ping                       — keepalive every 15 ticks (~15s)
- event: node_state_changed         — `{slug, node_id}` (nodes/<id>/state.md)
- event: chat_appended              — `{slug, node_id}` (nodes/<id>/chat.jsonl)
- event: orchestrator_appended      — `{slug}` (orchestrator.jsonl)
- event: awaiting_human             — `{slug, node_id}` (nodes/<id>/awaiting_human.md created)
- event: human_decision_received    — `{slug, node_id}` (nodes/<id>/human_decision.md created)
- event: job_state_changed          — `{slug}` (job.md)

Coalesce: at most one event per (slug, event_type, node_id) per 500ms
window. The poll cadence is 1s so practically each tick emits at most
one event per (key) anyway; the explicit cap exists so spammy mtime
storms (e.g. atomic rename rewrites) don't fan out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from dashboard_v2.settings import load_settings
from hammock_v2.engine import paths

log = logging.getLogger(__name__)

router = APIRouter()


_COALESCE_S = 0.5


def classify(rel_path: str) -> tuple[str, str | None] | None:
    """Map a relative path under the job dir to an (event_type, node_id) tuple.

    Returns None if the path isn't one we surface as an event.

    Public so tests and tooling can verify the mapping without going
    through the streaming endpoint.
    """
    if rel_path == "job.md":
        return ("job_state_changed", None)
    if rel_path == "orchestrator.jsonl":
        return ("orchestrator_appended", None)
    if rel_path.startswith("nodes" + os.sep):
        parts = rel_path.split(os.sep)
        if len(parts) < 3:
            return None
        node_id = parts[1]
        leaf = parts[2]
        if leaf == "state.md":
            return ("node_state_changed", node_id)
        if leaf == "chat.jsonl":
            return ("chat_appended", node_id)
        if leaf == "awaiting_human.md":
            return ("awaiting_human", node_id)
        if leaf == "human_decision.md":
            return ("human_decision_received", node_id)
    return None


async def _watch(slug: str, request: Request) -> AsyncIterator[str]:
    settings = load_settings()
    job_dir = paths.job_dir(slug, root=settings.root)
    seen: dict[str, float] = {}
    last_emit: dict[tuple[str, str | None], float] = {}

    def snapshot() -> dict[str, float]:
        out: dict[str, float] = {}
        if not job_dir.is_dir():
            return out
        for dirpath, _dirnames, filenames in os.walk(job_dir):
            for f in filenames:
                full = os.path.join(dirpath, f)
                try:
                    out[full] = os.path.getmtime(full)
                except OSError:
                    continue
        return out

    seen = snapshot()
    yield "event: ping\ndata: connected\n\n"

    last_ping = 0
    while True:
        if await request.is_disconnected():
            return
        try:
            current = snapshot()
            changed = [p for p, m in current.items() if seen.get(p, 0) != m]
            now = time.monotonic()
            for path in changed:
                rel = os.path.relpath(path, job_dir)
                kind = classify(rel)
                if kind is None:
                    continue
                event_type, node_id = kind
                key = (event_type, node_id)
                last = last_emit.get(key, 0.0)
                if now - last < _COALESCE_S:
                    continue
                last_emit[key] = now
                payload: dict[str, Any] = {"slug": slug}
                if node_id is not None:
                    payload["node_id"] = node_id
                yield f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
            seen = current
            last_ping += 1
            if last_ping >= 15:
                yield "event: ping\ndata: \n\n"
                last_ping = 0
        except Exception as exc:
            log.exception("sse watcher error: %s", exc)
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
        await asyncio.sleep(1)


@router.get("/jobs/{slug}")
async def stream_job(slug: str, request: Request) -> StreamingResponse:
    return StreamingResponse(_watch(slug, request), media_type="text/event-stream")
