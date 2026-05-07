"""Projects management endpoints — register / verify / delete.

Per ``docs/projects-management.md``: a project is a registered local
git checkout (``project.repo_path``). Code-kind workflows submit
against a registered project; the engine copies ``repo_path`` into
``<job_dir>/repo`` per job (no clone-from-remote).

This module owns the verify operations:

- repo_path exists, is a directory, contains ``.git/``
- ``git remote get-url origin`` → captured as ``remote_url``
- ``git symbolic-ref refs/remotes/origin/HEAD`` → captured as
  ``default_branch``, falling back to ``main`` then ``master``

Registration writes ``<root>/projects/<slug>/project.json``. We do not
touch the operator's working tree.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from dashboard.api.project_workflows import (
    ProjectWorkflowItem,
    verify_workflow_folder,
    list_workflows_for_project,
    project_repo_path,
    resolve_bundled_source,
)
from dashboard.state import projections
from dashboard.state.projections import ProjectDetail, ProjectListItem
from shared.atomic import atomic_write_text

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RegisterProjectRequest(BaseModel):
    """Body of ``POST /api/projects``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    """Absolute path to the operator's local git checkout."""

    slug: str | None = None
    """Override the auto-derived slug (folder basename, lowercased,
    hyphenated). Optional."""

    name: str | None = None
    """Override the auto-derived display name. Optional."""


class VerifyResult(BaseModel):
    """Outcome of the verify pipeline. Returned alongside the project
    detail on register and re-verify."""

    model_config = ConfigDict(extra="forbid")

    status: str  # "pass" | "warn" | "fail"
    remote_url: str | None
    default_branch: str | None
    reason: str | None = None
    """Free-form human message when status is ``warn`` or ``fail``."""


class RegisterProjectResponse(BaseModel):
    """Response body for ``POST /api/projects`` and the verify endpoint."""

    project: ProjectDetail
    verify: VerifyResult


# ---------------------------------------------------------------------------
# Verify pipeline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RawVerify:
    """Internal — verify result before it's wrapped in the API model."""

    status: str  # "pass" | "warn" | "fail"
    remote_url: str | None
    default_branch: str | None
    reason: str | None


def verify_repo_path(repo_path: Path) -> _RawVerify:
    """Run the verify pipeline against ``repo_path``.

    Steps (each may downgrade status):

    1. ``repo_path`` exists, is a directory, contains ``.git/``.
    2. ``git -C <repo_path> remote get-url origin`` → captured as
       ``remote_url``. Status ``fail`` if no origin remote.
    3. ``git -C <repo_path> symbolic-ref refs/remotes/origin/HEAD``
       (strip prefix) → captured as ``default_branch``. Status
       ``warn`` and fall back to ``main`` then ``master`` if not set.
    """
    if not repo_path.exists():
        return _RawVerify(
            status="fail",
            remote_url=None,
            default_branch=None,
            reason=f"path does not exist: {repo_path}",
        )
    if not repo_path.is_dir():
        return _RawVerify(
            status="fail",
            remote_url=None,
            default_branch=None,
            reason=f"path is not a directory: {repo_path}",
        )
    if not (repo_path / ".git").exists():
        return _RawVerify(
            status="fail",
            remote_url=None,
            default_branch=None,
            reason=f"path is not a git repo (no .git/): {repo_path}",
        )

    # Remote origin
    remote_proc = subprocess.run(
        ["git", "-C", str(repo_path), "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    if remote_proc.returncode != 0:
        return _RawVerify(
            status="fail",
            remote_url=None,
            default_branch=None,
            reason=f"no `origin` remote in {repo_path} (git: {remote_proc.stderr.strip()})",
        )
    remote_url = remote_proc.stdout.strip() or None
    if not remote_url:
        return _RawVerify(
            status="fail",
            remote_url=None,
            default_branch=None,
            reason=f"`origin` remote in {repo_path} has empty URL",
        )

    # Default branch
    sym_proc = subprocess.run(
        ["git", "-C", str(repo_path), "symbolic-ref", "refs/remotes/origin/HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if sym_proc.returncode == 0 and sym_proc.stdout.strip():
        # symbolic-ref returns either `refs/remotes/origin/<branch>` (the
        # production case after `git remote set-head origin -a`) or
        # `refs/heads/<branch>` (when origin/HEAD points at a local ref).
        # Strip whichever prefix is present.
        ref = sym_proc.stdout.strip()
        for prefix in ("refs/remotes/origin/", "refs/heads/"):
            if ref.startswith(prefix):
                ref = ref[len(prefix) :]
                break
        return _RawVerify(
            status="pass",
            remote_url=remote_url,
            default_branch=ref,
            reason=None,
        )

    # Fallback chain — symbolic-ref isn't set (no recent fetch). Try
    # main, then master, then accept the warning.
    for candidate in ("main", "master"):
        rev_proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "--verify", candidate],
            capture_output=True,
            text=True,
            check=False,
        )
        if rev_proc.returncode == 0:
            return _RawVerify(
                status="warn",
                remote_url=remote_url,
                default_branch=candidate,
                reason=(
                    "origin/HEAD not set; falling back to local "
                    f"{candidate}. Run `git remote set-head origin -a` to fix."
                ),
            )

    return _RawVerify(
        status="warn",
        remote_url=remote_url,
        default_branch="main",
        reason=(
            "could not determine default branch (origin/HEAD unset and "
            "no main/master). Defaulting to `main`."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SLUG_NORMALISE_RE = re.compile(r"[^a-z0-9-]+")


def derive_slug(path: Path, override: str | None = None) -> str:
    """Folder basename → lowercase hyphenated slug. Override wins."""
    if override:
        return override
    base = path.name.lower()
    cleaned = _SLUG_NORMALISE_RE.sub("-", base).strip("-")
    return cleaned or "project"


def _projects_dir(root: Path) -> Path:
    return root / "projects"


def _project_json_path(root: Path, slug: str) -> Path:
    return _projects_dir(root) / slug / "project.json"


def write_project_json(
    root: Path, slug: str, name: str, repo_path: Path, verify: _RawVerify
) -> ProjectDetail:
    """Persist ``<root>/projects/<slug>/project.json`` and return the
    projection. Used by both register and re-verify."""
    pj = _project_json_path(root, slug)
    pj.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    # Preserve created_at across re-verify; only register sets it fresh.
    created_at = now
    if pj.is_file():
        try:
            existing = json.loads(pj.read_text())
            if "created_at" in existing:
                created_at = datetime.fromisoformat(existing["created_at"])
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    data = {
        "slug": slug,
        "name": name,
        "repo_path": str(repo_path),
        "remote_url": verify.remote_url,
        "default_branch": verify.default_branch,
        "created_at": created_at.isoformat(),
        "last_health_check_at": now.isoformat(),
        "last_health_check_status": verify.status,
    }
    atomic_write_text(pj, json.dumps(data, indent=2))

    return ProjectDetail(
        slug=slug,
        name=name,
        repo_path=str(repo_path),
        remote_url=verify.remote_url,
        default_branch=verify.default_branch or "main",
        last_health_check_at=now,
        last_health_check_status=verify.status,  # type: ignore[arg-type]
    )


def _verify_to_response(raw: _RawVerify) -> VerifyResult:
    return VerifyResult(
        status=raw.status,
        remote_url=raw.remote_url,
        default_branch=raw.default_branch,
        reason=raw.reason,
    )


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ProjectListItem])
async def list_projects(request: Request) -> list[ProjectListItem]:
    """Enumerate registered projects on disk under ``<root>/projects/``."""
    settings = request.app.state.settings  # type: ignore[attr-defined]
    return projections.project_list(settings.root)


@router.get("/{slug}", response_model=ProjectDetail)
async def get_project(request: Request, slug: str) -> ProjectDetail:
    settings = request.app.state.settings  # type: ignore[attr-defined]
    detail = projections.project_detail(settings.root, slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")
    return detail


class CopyWorkflowRequest(BaseModel):
    """Body of ``POST /api/projects/{slug}/workflows/copy``."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    """Bundled workflow's ``job_type`` (folder name) to copy from."""

    dest_name: str | None = None
    """Destination folder name under ``<repo>/.hammock/workflows/``.
    Defaults to ``<source>-<project_slug>`` to avoid collision with
    bundled. Operator can pass an explicit name or rename the folder
    later by hand."""


class CopyWorkflowResponse(BaseModel):
    """Response body of the copy endpoint."""

    model_config = ConfigDict(extra="forbid")

    destination: str
    """Absolute path to the new workflow folder."""

    workflow: ProjectWorkflowItem
    """The freshly-listed workflow so the UI can refresh without a
    separate round-trip."""


@router.get("/{slug}/workflows", response_model=list[ProjectWorkflowItem])
async def list_project_workflows(request: Request, slug: str) -> list[ProjectWorkflowItem]:
    """Stage 5 — return bundled + project-local workflows for *slug*.

    The submit dropdown uses this. Each entry carries a ``valid`` flag
    plus an ``error`` reason when the workflow fails verification
    (missing prompt files, malformed yaml, schema_version mismatch);
    the dashboard hides invalid entries from the dropdown but lists
    them so the operator can fix them in their editor.
    """
    settings = request.app.state.settings  # type: ignore[attr-defined]
    items = list_workflows_for_project(settings.root, slug)
    if items is None:
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")
    return items


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


@router.post("", response_model=RegisterProjectResponse, status_code=201)
async def register_project(
    request: Request, body: RegisterProjectRequest
) -> RegisterProjectResponse:
    """Verify ``body.path`` is a usable git checkout, then write
    ``<root>/projects/<slug>/project.json``."""
    settings = request.app.state.settings  # type: ignore[attr-defined]
    root: Path = settings.root

    repo_path = Path(body.path).expanduser()
    raw = verify_repo_path(repo_path)
    if raw.status == "fail":
        raise HTTPException(status_code=400, detail=raw.reason or "verify failed")

    slug = derive_slug(repo_path, body.slug)
    if _project_json_path(root, slug).exists():
        raise HTTPException(
            status_code=409,
            detail=(
                f"project {slug!r} is already registered. DELETE it first or "
                "pass a different `slug`."
            ),
        )
    name = body.name or repo_path.name

    detail = write_project_json(root, slug, name, repo_path, raw)
    return RegisterProjectResponse(project=detail, verify=_verify_to_response(raw))


@router.delete("/{slug}", status_code=204)
async def delete_project(request: Request, slug: str) -> None:
    """Remove ``<root>/projects/<slug>/`` from disk. Does not touch
    any jobs that already reference this slug."""
    settings = request.app.state.settings  # type: ignore[attr-defined]
    root: Path = settings.root
    project_dir = _projects_dir(root) / slug
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")
    shutil.rmtree(project_dir)


@router.post("/{slug}/verify", response_model=RegisterProjectResponse)
async def reverify_project(request: Request, slug: str) -> RegisterProjectResponse:
    """Re-run verify against ``project.repo_path`` and update the
    project's ``last_health_check_*`` fields."""
    settings = request.app.state.settings  # type: ignore[attr-defined]
    root: Path = settings.root
    pj = _project_json_path(root, slug)
    if not pj.is_file():
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")

    try:
        existing = json.loads(pj.read_text())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"project.json malformed: {exc}") from exc
    repo_path = Path(existing.get("repo_path", ""))
    name = existing.get("name", slug)

    raw = verify_repo_path(repo_path)
    detail = write_project_json(root, slug, name, repo_path, raw)
    return RegisterProjectResponse(project=detail, verify=_verify_to_response(raw))


@router.post(
    "/{slug}/workflows/copy",
    response_model=CopyWorkflowResponse,
    status_code=201,
)
async def copy_workflow(
    request: Request, slug: str, body: CopyWorkflowRequest
) -> CopyWorkflowResponse:
    """Stage 6 — fork a bundled workflow into the project's repo.

    Recursive copy from ``hammock/templates/workflows/<source>/`` to
    ``<repo_path>/.hammock/workflows/<dest_name>/`` (default
    ``<source>-<slug>``). Returns 404 when the project or source
    doesn't exist; 409 when the destination already exists (we never
    silently overwrite — operator must delete first or pick a
    different ``dest_name``).

    The new folder is left for the operator's git workflow to add and
    commit. Hammock does not run ``git add`` or ``git commit``.
    """
    settings = request.app.state.settings  # type: ignore[attr-defined]
    root: Path = settings.root

    repo_path = project_repo_path(root, slug)
    if repo_path is None:
        raise HTTPException(status_code=404, detail=f"project {slug!r} not found")

    source_folder = resolve_bundled_source(body.source)
    if source_folder is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"bundled workflow {body.source!r} not found under hammock/templates/workflows/"
            ),
        )

    dest_name = body.dest_name or f"{body.source}-{slug}"
    dest_folder = repo_path / ".hammock" / "workflows" / dest_name
    if dest_folder.exists():
        raise HTTPException(
            status_code=409,
            detail=(
                f"destination {dest_folder} already exists. Delete it first or "
                "pass an explicit `dest_name` to avoid collision."
            ),
        )

    dest_folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_folder, dest_folder)

    # Build the response item from the just-copied folder.
    wf, error = verify_workflow_folder(dest_folder)
    item = ProjectWorkflowItem(
        job_type=dest_name,
        workflow_name=wf.workflow if wf is not None else None,
        source="custom",
        valid=error is None,
        error=error,
    )
    return CopyWorkflowResponse(destination=str(dest_folder), workflow=item)


__all__ = [
    "RegisterProjectRequest",
    "RegisterProjectResponse",
    "VerifyResult",
    "delete_project",
    "derive_slug",
    "register_project",
    "reverify_project",
    "router",
    "verify_repo_path",
    "write_project_json",
]
