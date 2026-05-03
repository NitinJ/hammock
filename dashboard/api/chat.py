"""Stage-15: POST /api/jobs/{job_slug}/stages/{stage_id}/chat.

Writes a human chat nudge into the stage's nudges.jsonl via the Channel
writer, making it available to the live session via ``--channels``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from dashboard.mcp.channel import Channel, NudgeMessage
from dashboard.state.cache import Cache

router = APIRouter(tags=["stage-actions"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1)


class ChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int
    timestamp: str
    kind: str
    text: str


def _assert_stage_exists(cache: Cache, job_slug: str, stage_id: str) -> None:
    """Raise 404 if the job or stage is not found in cache."""
    if cache.get_job(job_slug) is None:
        raise HTTPException(status_code=404, detail=f"job {job_slug!r} not found")
    if cache.get_stage(job_slug, stage_id) is None:
        raise HTTPException(
            status_code=404, detail=f"stage {stage_id!r} not found in job {job_slug!r}"
        )


@router.post(
    "/api/jobs/{job_slug}/stages/{stage_id}/chat",
    response_model=ChatResponse,
)
async def post_chat(
    request: Request, job_slug: str, stage_id: str, body: ChatRequest
) -> ChatResponse:
    """Push a human chat message into the running stage via nudges.jsonl."""
    cache: Cache = request.app.state.cache  # type: ignore[attr-defined]
    root = request.app.state.settings.root  # type: ignore[attr-defined]
    _assert_stage_exists(cache, job_slug, stage_id)

    channel = Channel(job_slug=job_slug, stage_id=stage_id, root=root)
    msg: NudgeMessage = await channel.push(kind="chat", text=body.text, source="human")
    return ChatResponse(
        seq=msg.seq,
        timestamp=msg.timestamp.isoformat(),
        kind=msg.kind,
        text=msg.text,
    )
