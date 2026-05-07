"""v1 compile endpoint — load + validate a workflow YAML, write job dir.

Per impl-patch §Stage 5: replaces the v0 plan-compiler pipeline (template
merging, param binding, stage validators). v1 jobs are defined by a
single workflow YAML; the engine validates it and seeds the job-request
variable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engine.v1.driver import JobSubmissionError, submit_job
from engine.v1.loader import WorkflowLoadError
from engine.v1.validator import WorkflowValidationError
from shared.v1.job import JobConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompileFailure:
    """A single failure surfaced to the caller."""

    kind: str  # "workflow_not_found" | "load" | "validation" | "submission" | "io"
    stage_id: str | None  # always None in v1; kept for HTTP response shape compat
    message: str


@dataclass(frozen=True)
class CompileSuccess:
    """Successful compile — job dir on disk."""

    job_slug: str
    job_dir: Path
    job_config: JobConfig
    stages: list[Any]  # always [] in v1; kept for HTTP response shape compat
    dry_run: bool


CompileResult = CompileSuccess | list[CompileFailure]


_SLUG_SAFE_RE = re.compile(r"[^a-z0-9-]+")


def _derive_slug(job_type: str, title: str, *, now: datetime | None = None) -> str:
    """v1 slug: ``<YYYY-MM-DD>-<job_type>-<title-slug>``. Lowercase,
    hyphenated, ASCII only."""
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
    title_slug = _SLUG_SAFE_RE.sub("-", title.lower()).strip("-")
    if not title_slug:
        title_slug = "untitled"
    return f"{stamp}-{job_type}-{title_slug}"


def compile_job(
    *,
    project_slug: str,
    job_type: str,
    title: str,
    request_text: str,
    root: Path,
    dry_run: bool = False,
    workflow_path: Path | None = None,
) -> CompileResult:
    """Compile a v1 job: locate the workflow YAML, validate, seed job dir.

    Args:
        project_slug: kept for v0-compat call sites; v1 derives repo
                      identity from the workflow's first code-kind node.
                      Stage 6 frontend may drop this.
        job_type: workflow name selector. ``"fix-bug"`` resolves to the
                  bundled ``hammock/templates/workflows/fix-bug.yaml``.
        title: human-readable title (used to derive slug).
        request_text: prose request seeded as the ``request`` variable.
        root: hammock root.
        dry_run: when True, validate only — do not write any state.
        workflow_path: explicit override for the workflow YAML path. When
                       set, ``job_type`` is ignored for resolution.
    """
    # 1. Resolve workflow path. Stage 5 — prefer the project-local
    # workflow under <repo_path>/.hammock/workflows/<job_type>/ over
    # the bundled one. Falls back to bundled when no project copy
    # exists (or when the operator hasn't picked a project — e.g.
    # dry_run probes from tests).
    wf_path = (
        workflow_path
        or _resolve_project_local_workflow(project_slug, job_type, root)
        or _resolve_bundled_workflow(job_type)
    )
    if wf_path is None:
        return [
            CompileFailure(
                kind="workflow_not_found",
                stage_id=None,
                message=(
                    f"no workflow found for job_type={job_type!r}; "
                    "pass workflow_path explicitly or add a bundled "
                    "workflow under hammock/templates/workflows/"
                ),
            )
        ]
    if not wf_path.is_file():
        return [
            CompileFailure(
                kind="workflow_not_found",
                stage_id=None,
                message=f"workflow YAML missing at {wf_path}",
            )
        ]

    if dry_run:
        # Validate only; don't write state.
        from engine.v1.loader import load_workflow
        from engine.v1.validator import assert_valid

        try:
            workflow = load_workflow(wf_path)
            assert_valid(workflow)
        except WorkflowLoadError as exc:
            return [CompileFailure(kind="load", stage_id=None, message=str(exc))]
        except WorkflowValidationError as exc:
            return [CompileFailure(kind="validation", stage_id=None, message=str(exc))]
        slug = _derive_slug(job_type, title)
        return CompileSuccess(
            job_slug=slug,
            job_dir=root / "jobs" / slug,
            job_config=JobConfig(
                job_slug=slug,
                workflow_name=workflow.workflow,
                workflow_path=str(wf_path.resolve()),
                state=__import__("shared.v1.job", fromlist=["JobState"]).JobState.SUBMITTED,
                repo_slug=project_slug or None,
                submitted_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            stages=[],
            dry_run=True,
        )

    # 2. Resolve repo identity from the registered project (when given).
    # Per docs/projects-management.md: for code-kind workflows we copy the
    # operator's local checkout (project.repo_path) into <job_dir>/repo
    # rather than cloning from a remote URL. project_slug → project.json
    # gives us repo_path + repo_slug + default_branch.
    repo_slug, repo_path, default_branch, repo_failure = _resolve_repo_identity(project_slug, root)
    if repo_failure is not None:
        return [repo_failure]

    # 3. Real submission via engine.v1.driver.
    slug = _derive_slug(job_type, title)
    try:
        cfg = submit_job(
            workflow_path=wf_path,
            request_text=request_text,
            job_slug=slug,
            root=root,
            repo_slug=repo_slug,
            repo_path=repo_path,
            default_branch=default_branch or "main",
        )
    except WorkflowLoadError as exc:
        return [CompileFailure(kind="load", stage_id=None, message=str(exc))]
    except WorkflowValidationError as exc:
        return [CompileFailure(kind="validation", stage_id=None, message=str(exc))]
    except JobSubmissionError as exc:
        return [CompileFailure(kind="submission", stage_id=None, message=str(exc))]

    return CompileSuccess(
        job_slug=slug,
        job_dir=root / "jobs" / slug,
        job_config=cfg,
        stages=[],
        dry_run=False,
    )


def _resolve_repo_identity(
    project_slug: str | None, root: Path
) -> tuple[str | None, Path | None, str | None, CompileFailure | None]:
    """Read ``<root>/projects/<slug>/project.json`` and return
    ``(repo_slug, repo_path, default_branch, failure)``.

    ``repo_slug`` is the ``owner/repo`` form derived from the project's
    ``remote_url`` (engine uses it for branch naming). ``repo_path`` is
    the operator's local checkout (engine copies it into
    ``<job_dir>/repo``). ``default_branch`` is the branch the engine
    forks ``hammock/jobs/<slug>`` off.

    Returns ``(None, None, None, None)`` when ``project_slug`` is
    empty — artifact-only workflows don't need a repo. Returns a
    ``CompileFailure`` when the slug is set but project.json is malformed.
    Missing project.json (or missing fields within it) is a soft-fail
    — the engine raises a clear error for code-kind workflows."""
    import json as _json

    from shared import paths as _shared_paths

    if not project_slug:
        return None, None, None, None

    pj = _shared_paths.project_json(project_slug, root=root)
    if not pj.is_file():
        log.info(
            "compile_job: project %r has no project.json at %s; submitting without repo identity",
            project_slug,
            pj,
        )
        return None, None, None, None
    try:
        data = _json.loads(pj.read_text())
    except Exception as exc:
        return (
            None,
            None,
            None,
            CompileFailure(
                kind="project_malformed",
                stage_id=None,
                message=f"could not parse {pj}: {exc}",
            ),
        )

    repo_path_str = data.get("repo_path") or ""
    repo_path = Path(repo_path_str) if repo_path_str else None
    remote_url = data.get("remote_url") or ""
    default_branch = data.get("default_branch") or "main"
    repo_slug = _derive_repo_slug(remote_url) if remote_url else project_slug
    return repo_slug, repo_path, default_branch, None


_GH_REPO_RE = re.compile(r"github\.com[/:](?P<owner>[^/]+)/(?P<repo>[^/.]+)")


def _derive_repo_slug(remote_url: str) -> str:
    """``https://github.com/owner/repo[.git]`` → ``owner/repo``.

    Falls back to the input string when the URL doesn't match the
    GitHub shape — non-GitHub remotes still need *some* slug for branch
    naming."""
    m = _GH_REPO_RE.search(remote_url)
    if m is None:
        return remote_url
    return f"{m.group('owner')}/{m.group('repo')}"


def _resolve_bundled_workflow(job_type: str) -> Path | None:
    """Locate the bundled workflow YAML for a given job_type.

    Looks under
    ``<repo>/hammock/templates/workflows/<job_type>/workflow.yaml``.
    Returns None on miss; caller surfaces as ``workflow_not_found``.
    """
    bundled = (
        Path(__file__).parent.parent.parent
        / "hammock"
        / "templates"
        / "workflows"
        / job_type
        / "workflow.yaml"
    )
    return bundled if bundled.is_file() else None


def _resolve_project_local_workflow(
    project_slug: str | None, job_type: str, root: Path
) -> Path | None:
    """Stage 5 — locate the project-local workflow under
    ``<project.repo_path>/.hammock/workflows/<job_type>/workflow.yaml``.

    Returns ``None`` when no project_slug is given, the project record
    is missing, or the path doesn't exist. Project-local resolution
    takes precedence over bundled when the project has a copy of the
    same job_type.
    """
    if not project_slug:
        return None
    pj = root / "projects" / project_slug / "project.json"
    if not pj.is_file():
        return None
    try:
        data = __import__("json").loads(pj.read_text())
    except (OSError, ValueError):
        return None
    repo_path = Path(data.get("repo_path", ""))
    candidate = repo_path / ".hammock" / "workflows" / job_type / "workflow.yaml"
    return candidate if candidate.is_file() else None


__all__ = [
    "CompileFailure",
    "CompileResult",
    "CompileSuccess",
    "compile_job",
]
