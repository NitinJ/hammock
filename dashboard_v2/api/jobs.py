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

from dashboard_v2 import projects as proj
from dashboard_v2 import workflows as wf_lib
from dashboard_v2.api.artifacts import save_artifacts
from dashboard_v2.api.projections import (
    append_orchestrator_message,
    job_summary,
    list_jobs,
    load_workflow_or_none,
    node_chat,
    node_detail,
    orchestrator_chat,
    orchestrator_events,
    orchestrator_messages,
    write_human_decision,
)
from dashboard_v2.jobs import lifecycle as lifecycle_lib
from dashboard_v2.runner.spawn import spawn_orchestrator
from dashboard_v2.settings import load_settings
from hammock_v2.engine import paths
from hammock_v2.engine.runner import JobConfig
from hammock_v2.engine.runner import submit_job as _submit_job_setup

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
    project_slug_form: str | None = Form(default=None, alias="project_slug"),
    artifacts: list[UploadFile] = File(default_factory=list),  # noqa: B008  fastapi default
) -> JobSubmitResponse:
    """Submit a job. Accepts either:

    - ``application/json`` with ``{workflow, request, project_slug?}`` (no artifacts).
    - ``multipart/form-data`` with ``workflow``, ``request``,
      ``project_slug?``, and any number of ``artifacts`` files.

    ``project_slug`` is preferred over the env-var fallback. When
    supplied, the runner clones the project's ``repo_path`` into
    ``<job_dir>/repo`` and prefers project-local workflows
    (``<repo>/.hammock-v2/workflows/<name>.yaml``) over bundled.
    """
    content_type = request.headers.get("content-type", "")
    body_workflow = workflow
    body_request = request_text
    body_project_slug = project_slug_form
    files: list[tuple[str, bytes]] = []
    if content_type.startswith("application/json"):
        try:
            payload = await request.json()
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc
        body_workflow = payload.get("workflow")
        body_request = payload.get("request")
        body_project_slug = payload.get("project_slug") or body_project_slug
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
    project_repo_path = settings.project_repo_path
    if body_project_slug:
        try:
            project_slug = proj.normalize_slug(body_project_slug)
        except proj.ProjectError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        project_data = proj.read_project(project_slug, settings.root)
        if project_data is None:
            raise HTTPException(status_code=400, detail=f"project {project_slug!r} not registered")
        from pathlib import Path as _P

        project_repo_path = _P(project_data["repo_path"])
    workflow_path = wf_lib.resolve_at_submit(
        body_workflow,
        root=settings.root,
        project_slug=body_project_slug if body_project_slug else None,
    )
    if workflow_path is None:
        raise HTTPException(status_code=400, detail=f"workflow {body_workflow!r} not found")
    wf = load_workflow_or_none(body_workflow, override_path=workflow_path)
    if wf is None:
        raise HTTPException(status_code=400, detail=f"workflow {body_workflow!r} failed to load")
    slug = _derive_slug(body_workflow, body_request)
    if files:
        try:
            save_artifacts(slug=slug, files=files, root=settings.root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Set up job dir synchronously so the dashboard sees the layout
    # immediately on POST return (clone the project repo, snapshot the
    # workflow, write job.md). The orchestrator subprocess will skip
    # re-submission since the dir exists.
    _submit_job_setup(
        job=JobConfig(
            slug=slug,
            workflow_name=body_workflow,
            request_text=body_request,
            project_repo_path=project_repo_path,
        ),
        workflow_path=workflow_path,
        root=settings.root,
    )
    pid = spawn_orchestrator(
        slug=slug,
        workflow_name=body_workflow,
        request_text=body_request,
        root=settings.root,
        project_repo_path=project_repo_path,
        claude_binary=settings.claude_binary,
        runner_mode=settings.runner_mode,
        workflow_path=workflow_path,
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


@router.get("/jobs/{slug}/orchestrator/events")
def get_orchestrator_events(slug: str) -> dict[str, Any]:
    settings = load_settings()
    if not paths.job_dir(slug, root=settings.root).is_dir():
        raise HTTPException(status_code=404, detail=f"job {slug!r} not found")
    return {"events": orchestrator_events(slug, root=settings.root)}


class OrchestratorMessageRequest(BaseModel):
    text: str = Field(..., min_length=1)


@router.get("/jobs/{slug}/orchestrator/messages")
def get_orchestrator_messages(slug: str) -> dict[str, Any]:
    settings = load_settings()
    if not paths.job_dir(slug, root=settings.root).is_dir():
        raise HTTPException(status_code=404, detail=f"job {slug!r} not found")
    return {"messages": orchestrator_messages(slug, root=settings.root)}


@router.post("/jobs/{slug}/orchestrator/messages")
def post_orchestrator_message(slug: str, body: OrchestratorMessageRequest) -> dict[str, Any]:
    settings = load_settings()
    if not paths.job_dir(slug, root=settings.root).is_dir():
        raise HTTPException(status_code=404, detail=f"job {slug!r} not found")
    try:
        msg = append_orchestrator_message(
            slug=slug, text=body.text, sender="operator", root=settings.root
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": "true", "message": msg}


@router.post("/jobs/{slug}/pause")
def post_pause(slug: str) -> dict[str, str]:
    settings = load_settings()
    try:
        return lifecycle_lib.pause_job(slug, root=settings.root)
    except lifecycle_lib.LifecycleError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.post("/jobs/{slug}/resume")
def post_resume(slug: str) -> dict[str, str]:
    settings = load_settings()
    try:
        return lifecycle_lib.resume_job(slug, root=settings.root)
    except lifecycle_lib.LifecycleError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.post("/jobs/{slug}/stop")
def post_stop(slug: str) -> dict[str, str]:
    settings = load_settings()
    try:
        return lifecycle_lib.stop_job(slug, root=settings.root)
    except lifecycle_lib.LifecycleError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.delete("/jobs/{slug}")
def delete_job(slug: str) -> dict[str, str]:
    settings = load_settings()
    try:
        return lifecycle_lib.delete_job(slug, root=settings.root)
    except lifecycle_lib.LifecycleError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


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
