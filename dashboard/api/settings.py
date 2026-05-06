"""``GET /api/settings`` — minimal v1 operator info.

Stage 3 retains a barebones settings endpoint for parity with the
existing frontend's settings page. The v0 rich rollup (active jobs,
project inventories, MCP descriptors) is rebuilt as needed in Stage 6's
frontend / backend rewrite.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_mode: str
    claude_binary: str | None
    root: str


@router.get("", response_model=SettingsView)
async def get_settings(request: Request) -> SettingsView:
    settings = request.app.state.settings  # type: ignore[attr-defined]
    return SettingsView(
        runner_mode=settings.runner_mode,
        claude_binary=settings.claude_binary if settings.runner_mode == "real" else None,
        root=str(settings.root),
    )
