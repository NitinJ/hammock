"""Job + node + chat + HIL endpoints for v2 dashboard."""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import secrets
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from dashboard_v2.api.artifacts import save_artifacts
from dashboard_v2.api.projections import (
    job_summary,
    list_jobs,
    load_workflow_or_none,
    node_chat,
    node_detail,
    orchestrator_chat,
    write_human_decision,
)
from dashboard_v2.runner.spawn import spawn_orchestrator
from dashboard_v2.settings import load_settings
from hammock_v2.engine import paths

log = logging.getLogger(__name__)

router = APIRouter()

_SLUG_SAFE_RE = re.compile(r"[^a-z0-9-]+")


def _derive_slug(workflow_name: str, request_text: str) -> str:
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%d")
    head = request_text.strip().lower()[:60]
    head_slug = _SLUG_SAFE_RE.sub("-", head).strip("-") or "job"
    suffix = secrets.token_hex(3)
    return f"{stamp}-{workflow_name}-{head_slug}-{suffix}"


# -------------------- Models --------------------


class JobSubmitRequest(BaseModel):
    workflow: str = Field(..., min_length=1)
    request: str = Field(..., min_length=1)


class JobSubmitResponse(BaseModel):
    slug: str
    pid: int


class HumanDecisionRequest(BaseModel):
    decision: str
    comment: str | None = None


# -------------------- Endpoints --------------------


@router.post("/jobs", response_model=JobSubmitResponse)
async def submit_job(
    request: Request,
    workflow: str | None = Form(default=None),
    request_text: str | None = Form(default=None, alias="request"),
    artifacts: list[UploadFile] = File(default_factory=list),  # noqa: B008  fastapi default
) -> JobSubmitResponse:
    """Submit a job. Accepts either:

    - ``application/json`` with ``{workflow, request}`` (no artifacts).
    - ``multipart/form-data`` with ``workflow``, ``request``, and any
      number of ``artifacts`` files.
    """
    content_type = request.headers.get("content-type", "")
    body_workflow = workflow
    body_request = request_text
    files: list[tuple[str, bytes]] = []
    if content_type.startswith("application/json"):
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc
        body_workflow = payload.get("workflow")
        body_request = payload.get("request")
    elif content_type.startswith("multipart/form-data") or content_type.startswith(
        "application/x-www-form-urlencoded"
    ):
        for upload in artifacts:
            content = await upload.read()
            files.append((upload.filename or "artifact", content))
    else:
        raise HTTPException(
            status_code=415,
            detail="content-type must be application/json or multipart/form-data",
        )
    if not body_workflow:
        raise HTTPException(status_code=400, detail="workflow is required")
    if not body_request or not body_request.strip():
        raise HTTPException(status_code=400, detail="request is required")
    settings = load_settings()
    wf = load_workflow_or_none(body_workflow, root=settings.root)
    if wf is None:
        raise HTTPException(status_code=400, detail=f"workflow {body_workflow!r} not found")
    slug = _derive_slug(body_workflow, body_request)
    if files:
        try:
            save_artifacts(slug=slug, files=files, root=settings.root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    pid = spawn_orchestrator(
        slug=slug,
        workflow_name=body_workflow,
        request_text=body_request,
        root=settings.root,
        project_repo_path=settings.project_repo_path,
        claude_binary=settings.claude_binary,
        runner_mode=settings.runner_mode,
    )
    return JobSubmitResponse(slug=slug, pid=pid)


@router.get("/jobs")
def get_jobs() -> dict[str, Any]:
    settings = load_settings()
    return {"jobs": list_jobs(settings.root)}


@router.get("/jobs/{slug}")
def get_job(slug: str) -> dict[str, Any]:
    settings = load_settings()
    summary = job_summary(slug, root=settings.root)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"job {slug!r} not found")
    return summary


@router.get("/jobs/{slug}/nodes/{node_id}")
def get_node(slug: str, node_id: str) -> dict[str, Any]:
    settings = load_settings()
    detail = node_detail(slug, node_id, root=settings.root)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"node {node_id!r} of job {slug!r} not found")
    return detail


@router.get("/jobs/{slug}/nodes/{node_id}/chat")
def get_node_chat(slug: str, node_id: str) -> dict[str, Any]:
    settings = load_settings()
    turns = node_chat(slug, node_id, root=settings.root)
    return {"turns": turns, "has_chat": bool(turns)}


@router.get("/jobs/{slug}/orchestrator/chat")
def get_orchestrator_chat(slug: str) -> dict[str, Any]:
    settings = load_settings()
    turns = orchestrator_chat(slug, root=settings.root)
    return {"turns": turns, "has_chat": bool(turns)}


@router.post("/jobs/{slug}/nodes/{node_id}/human_decision")
def post_human_decision(slug: str, node_id: str, body: HumanDecisionRequest) -> dict[str, str]:
    settings = load_settings()
    if not paths.node_dir(slug, node_id, root=settings.root).is_dir():
        raise HTTPException(status_code=404, detail="node not found")
    try:
        target = write_human_decision(
            slug=slug,
            node_id=node_id,
            decision=body.decision,
            comment=body.comment,
            root=settings.root,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": str(target), "ok": "true"}
