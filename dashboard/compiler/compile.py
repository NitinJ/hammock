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
    # 1. Resolve workflow path.
    wf_path = workflow_path or _resolve_bundled_workflow(job_type)
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

    # 2. Real submission via engine.v1.driver.
    slug = _derive_slug(job_type, title)
    try:
        cfg = submit_job(
            workflow_path=wf_path,
            request_text=request_text,
            job_slug=slug,
            root=root,
            repo_slug=project_slug or None,
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


def _resolve_bundled_workflow(job_type: str) -> Path | None:
    """Locate the bundled workflow YAML for a given job_type.

    Looks under ``<repo>/hammock/templates/workflows/<job_type>.yaml``.
    Returns None on miss; caller surfaces as ``workflow_not_found``.
    """
    bundled = (
        Path(__file__).parent.parent.parent
        / "hammock"
        / "templates"
        / "workflows"
        / f"{job_type}.yaml"
    )
    return bundled if bundled.is_file() else None


__all__ = [
    "CompileFailure",
    "CompileResult",
    "CompileSuccess",
    "compile_job",
]
