"""Job endpoints: list, detail, and submit.

Per impl-patch §Stage 3: every handler reads disk directly via the
pure-function projections in ``dashboard.state.projections``. No cache.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from dashboard.code.branches import create_job_branch
from dashboard.compiler.compile import compile_job
from dashboard.driver.lifecycle import spawn_driver
from dashboard.state import projections
from dashboard.state.chat import read_agent_chat
from dashboard.state.projections import JobDetail, JobListItem, NodeDetail
from shared import paths
from shared.models import ProjectConfig
from shared.v1 import paths as v1_paths
from shared.v1.job import JobState

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ---------------------------------------------------------------------------
# Submit (POST /api/jobs) — request / response shapes
# ---------------------------------------------------------------------------


class JobSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_slug: str = ""
    """v0-compat field. v1 derives repo identity from the workflow's
    first code-kind node, so for artifact-only workflows this can stay
    empty. When set, the dashboard reads ``<root>/projects/<slug>/
    project.json`` to locate the repo for branch creation."""

    job_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    request_text: str = Field(min_length=1)
    dry_run: bool = False


class CompileFailureOut(BaseModel):
    kind: str
    stage_id: str | None
    message: str


class JobSubmitResponse(BaseModel):
    job_slug: str
    dry_run: bool
    stages: list[dict[str, Any]] | None = None


class AgentChatResponse(BaseModel):
    """Per-node chat transcript response.

    ``turns`` is the raw list of stream-json objects (system / assistant
    / user / result) the agent emitted on this attempt. ``has_chat`` is
    False when ``chat.jsonl`` doesn't exist on disk — old jobs (with
    plain-text ``stdout.log``) and not-yet-run nodes both look the same
    to the frontend, which surfaces "no transcript" in either case.
    """

    model_config = ConfigDict(extra="forbid")
    turns: list[dict[str, Any]] = Field(default_factory=list)
    attempt: int
    has_chat: bool


@router.get("", response_model=list[JobListItem])
async def list_jobs(
    request: Request,
    repo_slug: Annotated[str | None, Query(description="filter by repo slug (owner/repo)")] = None,
    state: Annotated[JobState | None, Query(description="filter by job state")] = None,
) -> list[JobListItem]:
    settings = request.app.state.settings  # type: ignore[attr-defined]
    return projections.job_list(settings.root, repo_slug=repo_slug, state=state)


@router.get("/{job_slug}", response_model=JobDetail)
async def get_job(request: Request, job_slug: str) -> JobDetail:
    settings = request.app.state.settings  # type: ignore[attr-defined]
    detail = projections.job_detail(settings.root, job_slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"job {job_slug!r} not found")
    return detail


@router.get("/{job_slug}/nodes/{node_id}/chat", response_model=AgentChatResponse)
async def get_node_chat(
    request: Request,
    job_slug: str,
    node_id: str,
    attempt: Annotated[int, Query(ge=1, description="attempt number (default 1)")] = 1,
) -> AgentChatResponse:
    """Per-node chat transcript: parse the agent's stream-json output.

    Always 200; the file's absence is signalled via ``has_chat=False``
    in the response body so the frontend distinguishes 'no transcript
    yet' from 'job/node not found'.
    """
    settings = request.app.state.settings  # type: ignore[attr-defined]
    turns = read_agent_chat(settings.root, job_slug, node_id, attempt=attempt)
    has_chat = (
        v1_paths.node_attempt_dir(job_slug, node_id, attempt, root=settings.root)
        / "chat.jsonl"
    ).is_file()
    return AgentChatResponse(turns=turns, attempt=attempt, has_chat=has_chat)


@router.get("/{job_slug}/nodes/{node_id}", response_model=NodeDetail)
async def get_node(request: Request, job_slug: str, node_id: str) -> NodeDetail:
    """Per-node drilldown: state + envelopes produced by this node id.

    Note: for loop body nodes, ``state.json`` reflects the latest
    iteration only; the per-iteration state is reconstructed from
    envelope existence in the parent loop's iteration row of
    ``GET /api/jobs/{slug}``."""
    settings = request.app.state.settings  # type: ignore[attr-defined]
    detail = projections.node_detail(settings.root, job_slug, node_id)
    if detail is None:
        raise HTTPException(
            status_code=404, detail=f"no node {node_id!r} on disk for job {job_slug!r}"
        )
    return detail


@router.post("", response_model=JobSubmitResponse, status_code=201)
async def submit_job(body: JobSubmitRequest, request: Request) -> JobSubmitResponse:
    settings = request.app.state.settings  # type: ignore[attr-defined]

    result = compile_job(
        project_slug=body.project_slug,
        job_type=body.job_type,
        title=body.title,
        request_text=body.request_text,
        root=settings.root,
        dry_run=body.dry_run,
    )

    if isinstance(result, list):
        raise HTTPException(
            status_code=422,
            detail=[{"kind": f.kind, "stage_id": f.stage_id, "message": f.message} for f in result],
        )

    if not result.dry_run:
        try:
            _create_job_branch_best_effort(
                project_slug=body.project_slug,
                job_slug=result.job_slug,
                root=settings.root,
            )
        except subprocess.CalledProcessError as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"failed to create hammock/jobs/{result.job_slug} in "
                    f"{body.project_slug!r}: {exc}. The job dir is on disk "
                    "but no driver was spawned. Investigate the git error and "
                    "either re-submit or spawn the driver manually."
                ),
            ) from exc
        await spawn_driver(
            result.job_slug,
            root=settings.root,
            fake_fixtures_dir=settings.fake_fixtures_dir,
            claude_binary=settings.claude_binary,
        )
        return JobSubmitResponse(job_slug=result.job_slug, dry_run=False)

    stages_out = [s.model_dump(mode="json") for s in result.stages]
    return JobSubmitResponse(job_slug=result.job_slug, dry_run=True, stages=stages_out)


def _create_job_branch_best_effort(
    *,
    project_slug: str,
    job_slug: str,
    root: Path | None,
) -> None:
    """Read project.json + create ``hammock/jobs/<slug>`` in the repo.

    Silenced (logged, not raised):
    - project.json missing or unreadable.
    - repo_path doesn't exist on disk.
    - repo_path isn't a git repo (no ``.git/``).

    Propagates:
    - Any ``CalledProcessError`` from ``git branch`` against an
      otherwise-valid registered repo.
    """
    try:
        project = ProjectConfig.model_validate_json(
            paths.project_json(project_slug, root=root).read_text()
        )
    except (FileNotFoundError, ValueError) as exc:
        log.warning(
            "could not read project.json for %s: %s — skipping job-branch creation",
            project_slug,
            exc,
        )
        return

    repo = Path(project.repo_path)
    if not (repo / ".git").exists():
        log.warning(
            "%s is not a git repo — skipping job-branch creation for %s",
            repo,
            job_slug,
        )
        return

    create_job_branch(repo, job_slug, base=project.default_branch)
