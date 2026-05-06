"""HIL endpoints — v1 thin handler.

Per impl-patch §Stage 3 / §9.8: ``dashboard/api/hil.py`` is a thin
FastAPI wrapper over the engine's HIL submission paths. Both explicit
HIL gates (workflow-declared ``actor: human`` nodes) and implicit asks
(``ask_human`` MCP calls) surface here.

URL shape (v1):

  GET  /api/hil                                  → all pending across jobs (explicit + implicit)
  GET  /api/hil/{job_slug}                       → pending for one job
  GET  /api/hil/{job_slug}/{node_id}             → one explicit-pending detail
  POST /api/hil/{job_slug}/{node_id}/answer      → submit explicit answer ({var_name, value})
  GET  /api/hil/{job_slug}/asks/{call_id}        → one implicit-ask detail
  POST /api/hil/{job_slug}/asks/{call_id}/answer → submit implicit answer ({answer: str})

Explicit submission validates against the variable type's Value schema
via the engine's ``submit_hil_answer``. Implicit submission rewrites
the ask marker JSON in-place to add an ``answer`` field; the per-job
MCP server polls the marker and unblocks the agent on the change.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dashboard.state import projections
from dashboard.state.projections import HilQueueItem
from engine.v1.hil import HilSubmissionError, submit_hil_answer
from engine.v1.loader import WorkflowLoadError, load_workflow
from shared.atomic import atomic_write_text
from shared.v1 import paths as v1_paths
from shared.v1.job import JobConfig

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/hil", tags=["hil"])


class HilAnswerRequest(BaseModel):
    """Body of POST /api/hil/{job_slug}/{node_id}/answer."""

    model_config = ConfigDict(extra="forbid")

    var_name: str = Field(min_length=1)
    """The output variable being submitted (must be one of the node's
    declared outputs in the workflow)."""

    value: dict[str, Any] = Field(default_factory=dict)
    """Typed payload conforming to the variable type's Value schema.
    The engine's submit_hil_answer runs the type's ``produce`` to
    validate; on failure the response is HTTP 400 with the message."""


class HilAnswerResponse(BaseModel):
    """Response shape: just the gate identity for confirmation."""

    job_slug: str
    node_id: str
    var_name: str


class AskAnswerRequest(BaseModel):
    """Body of POST /api/hil/{job_slug}/asks/{call_id}/answer."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    """Free-form prose returned to the agent's ``ask_human`` call."""


class AskAnswerResponse(BaseModel):
    job_slug: str
    call_id: str


@router.get("", response_model=list[HilQueueItem])
async def list_hil(
    request: Request,
    job: Annotated[str | None, Query(description="filter by job slug")] = None,
) -> list[HilQueueItem]:
    settings = request.app.state.settings  # type: ignore[attr-defined]
    return projections.hil_queue(settings.root, job_slug=job)


@router.get("/{job_slug}", response_model=list[HilQueueItem])
async def list_hil_for_job(request: Request, job_slug: str) -> list[HilQueueItem]:
    settings = request.app.state.settings  # type: ignore[attr-defined]
    return projections.hil_queue(settings.root, job_slug=job_slug)


@router.get("/{job_slug}/asks/{call_id}", response_model=HilQueueItem)
async def get_ask(request: Request, job_slug: str, call_id: str) -> HilQueueItem:
    """Detail for one implicit ``ask_human`` marker."""
    settings = request.app.state.settings  # type: ignore[attr-defined]
    item = projections.hil_queue_ask(settings.root, job_slug, call_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"no pending ask for {job_slug!r}/{call_id!r}",
        )
    return item


@router.get("/{job_slug}/{node_id}", response_model=HilQueueItem)
async def get_hil(request: Request, job_slug: str, node_id: str) -> HilQueueItem:
    settings = request.app.state.settings  # type: ignore[attr-defined]
    item = projections.hil_queue_item(settings.root, job_slug, node_id)
    if item is None:
        raise HTTPException(
            status_code=404,
            detail=f"no pending HIL for {job_slug!r}/{node_id!r}",
        )
    return item


@router.post(
    "/{job_slug}/asks/{call_id}/answer",
    response_model=AskAnswerResponse,
)
async def submit_ask_answer(
    request: Request,
    job_slug: str,
    call_id: str,
    body: AskAnswerRequest,
) -> AskAnswerResponse:
    """Answer a pending implicit ask. Rewrites the marker to add the
    ``answer`` field; the per-job MCP server polls and returns the
    string to its agent caller."""
    settings = request.app.state.settings  # type: ignore[attr-defined]
    root: Path = settings.root
    marker = v1_paths.job_dir(job_slug, root=root) / "asks" / f"{call_id}.json"
    if not marker.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"no pending ask for {job_slug!r}/{call_id!r}",
        )
    try:
        data = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"ask marker malformed: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="ask marker has wrong shape")
    if "answer" in data:
        raise HTTPException(status_code=409, detail="ask already answered")
    data["answer"] = body.answer
    atomic_write_text(marker, json.dumps(data, indent=2))
    return AskAnswerResponse(job_slug=job_slug, call_id=call_id)


@router.post("/{job_slug}/{node_id}/answer", response_model=HilAnswerResponse)
async def submit_answer(
    request: Request,
    job_slug: str,
    node_id: str,
    body: HilAnswerRequest,
) -> HilAnswerResponse:
    settings = request.app.state.settings  # type: ignore[attr-defined]
    root: Path = settings.root

    # Load the workflow for this job — the engine needs it to resolve
    # node declarations + inputs during submission.
    cfg_path = v1_paths.job_config_path(job_slug, root=root)
    if not cfg_path.is_file():
        raise HTTPException(status_code=404, detail=f"job {job_slug!r} not found")
    try:
        cfg = JobConfig.model_validate_json(cfg_path.read_text())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"job.json malformed: {exc}") from exc

    try:
        workflow = load_workflow(Path(cfg.workflow_path))
    except WorkflowLoadError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"could not load workflow for {job_slug!r}: {exc}",
        ) from exc

    try:
        submit_hil_answer(
            job_slug=job_slug,
            node_id=node_id,
            var_name=body.var_name,
            value_payload=body.value,
            root=root,
            workflow=workflow,
        )
    except HilSubmissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return HilAnswerResponse(
        job_slug=job_slug,
        node_id=node_id,
        var_name=body.var_name,
    )
