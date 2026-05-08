"""Tiny SSE: poll the job dir mtime tree, push events on change.

Wire format: text/event-stream with named events.
- event: ping        — keepalive every 15s
- event: job_changed — payload is JSON {slug}
- event: node_changed — payload is JSON {slug, node_id, file}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from dashboard_v2.settings import load_settings
from hammock_v2.engine import paths

log = logging.getLogger(__name__)

router = APIRouter()


async def _watch(slug: str, request: Request) -> AsyncIterator[str]:
    settings = load_settings()
    job_dir = paths.job_dir(slug, root=settings.root)
    seen: dict[str, float] = {}

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
            for path in changed:
                rel = os.path.relpath(path, job_dir)
                if rel.startswith("nodes" + os.sep):
                    parts = rel.split(os.sep)
                    if len(parts) >= 3:
                        payload: dict[str, Any] = {
                            "slug": slug,
                            "node_id": parts[1],
                            "file": parts[2],
                        }
                        yield f"event: node_changed\ndata: {json.dumps(payload)}\n\n"
                elif rel == "job.md" or rel.startswith("orchestrator."):
                    yield f"event: job_changed\ndata: {json.dumps({'slug': slug, 'file': rel})}\n\n"
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
