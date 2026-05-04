"""Plan Compiler entry point: ``compile_job(...)``.

Pipeline:
1. Resolve the project from the registry.
2. Load global template + per-project override (if present).
3. Modify-only deep-merge.
4. Param-bind ``${...}`` placeholders.
5. Pydantic-validate stages (via ``StageDefinition``).
6. Run structural validators (DAG closure, loop_back, predicates, ...).
7. Generate ``job_slug``; create job dir.
8. Atomically write ``prompt.md``, ``stage-list.yaml``, ``job.json``.
9. Return the success result.

Failures collected at any stage are returned without writing.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import ValidationError

from dashboard.compiler.overrides import OverrideFailure, merge_overrides
from dashboard.compiler.validators import ValidationFailure, validate_plan
from shared import paths
from shared.atomic import atomic_write_json, atomic_write_text
from shared.models import JobConfig, JobState, ProjectConfig, StageDefinition
from shared.slug import (
    SlugDerivationError,
    derive_slug,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompileFailure:
    """A single failure surfaced to the caller."""

    kind: str  # "project_not_found" | "template_not_found" | "override" |
    #          "schema" | "validation" | "param_binding" | "io"
    stage_id: str | None
    message: str


@dataclass(frozen=True)
class CompileSuccess:
    """Successful compile — job dir written (or planned, if ``dry_run``)."""

    job_slug: str
    job_dir: Path
    job_config: JobConfig
    stages: list[StageDefinition]
    dry_run: bool


CompileResult = CompileSuccess | list[CompileFailure]


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------


# Default location for the bundled global templates (the package ships them
# under ``hammock/templates/job-templates/``). Production code should copy
# these to ``~/.hammock/job-templates/`` on first run; tests pass an explicit
# ``templates_dir``.
_BUNDLED_TEMPLATES_DIR = (
    Path(__file__).parent.parent.parent / "hammock" / "templates" / "job-templates"
)


def compile_job(
    *,
    project_slug: str,
    job_type: str,
    title: str,
    request_text: str,
    root: Path | None = None,
    templates_dir: Path | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> CompileResult:
    """Compile a job submission into a job directory (or fail with reasons).

    Returns either a :class:`CompileSuccess` or a non-empty
    ``list[CompileFailure]`` — never both.
    """
    failures: list[CompileFailure] = []

    # 1. Project must exist
    project = _load_project(project_slug, root=root)
    if project is None:
        return [
            CompileFailure(
                "project_not_found",
                None,
                f"no project registered with slug {project_slug!r}",
            )
        ]

    # 2. Resolve templates dir
    template_path = _resolve_template(job_type, templates_dir=templates_dir, root=root)
    if template_path is None:
        return [
            CompileFailure(
                "template_not_found",
                None,
                f"no job template named {job_type!r} (looked in user dir + bundled)",
            )
        ]

    # 3. Load template + (optional) per-project override
    try:
        global_template = yaml.safe_load(template_path.read_text())
    except (yaml.YAMLError, OSError) as e:
        return [
            CompileFailure(
                "template_not_found", None, f"could not parse template {template_path}: {e}"
            )
        ]

    override_path = _project_override_path(project, job_type)
    override = None
    if override_path is not None and override_path.exists():
        try:
            override = yaml.safe_load(override_path.read_text())
        except (yaml.YAMLError, OSError) as e:
            return [
                CompileFailure("override", None, f"could not parse override {override_path}: {e}")
            ]

    # 4. Merge (modify-only)
    merged, override_failures = merge_overrides(global_template, override)
    if override_failures:
        return [_override_to_compile(f) for f in override_failures]

    # 5. Bind params (need a job_slug to substitute ${job.slug}; derive it now)
    if now is None:
        now = datetime.now(UTC)
    try:
        job_slug = _generate_job_slug(title, root=root, now=now)
    except SlugDerivationError as e:
        return [
            CompileFailure(
                "param_binding",
                None,
                f"cannot derive a job slug from title {title!r}: {e}",
            )
        ]
    job_id = uuid4().hex
    context = {
        "job": {
            "id": job_id,
            "slug": job_slug,
            "title": title,
            "type": job_type,
        },
        "project": {
            "slug": project.slug,
            "name": project.name,
        },
    }
    bound, binding_failures = _bind_params(merged, context)
    if binding_failures:
        return [CompileFailure("param_binding", None, msg) for msg in binding_failures]

    # 6. Pydantic-validate stages
    raw_stages = bound.get("stages", [])
    stages: list[StageDefinition] = []
    for i, raw in enumerate(raw_stages):
        try:
            stages.append(StageDefinition.model_validate(raw))
        except ValidationError as e:
            sid = raw.get("id") if isinstance(raw, dict) else None
            failures.append(
                CompileFailure(
                    "schema",
                    sid,
                    f"stage at position {i} fails Pydantic validation: {e}",
                )
            )
    if failures:
        return failures

    # 7. Structural validation
    validation_failures = validate_plan(stages)
    if validation_failures:
        return [_validation_to_compile(f) for f in validation_failures]

    # 8. Build JobConfig
    job_config = JobConfig(
        job_id=job_id,
        job_slug=job_slug,
        project_slug=project.slug,
        job_type=job_type,
        created_at=now,
        created_by="cli",
        state=JobState.SUBMITTED,
    )

    job_dir = paths.job_dir(job_slug, root=root)

    if dry_run:
        return CompileSuccess(
            job_slug=job_slug,
            job_dir=job_dir,
            job_config=job_config,
            stages=stages,
            dry_run=True,
        )

    # 9. Write atomically
    try:
        job_dir.mkdir(parents=True, exist_ok=False)
        atomic_write_json(paths.job_json(job_slug, root=root), job_config)
        atomic_write_text(paths.job_prompt(job_slug, root=root), request_text)
        # stage-list.yaml is the merged + bound template (after Pydantic
        # validation) re-serialised. Use the dict form to preserve the
        # template's top-level keys (description, etc.).
        atomic_write_text(
            paths.job_stage_list(job_slug, root=root),
            yaml.safe_dump(bound, sort_keys=False),
        )
        # v0 alignment Plan #3: snapshot the project's specialist
        # catalogue at compile time so every stage of this job sees
        # the same agents/skills, even if the operator edits an
        # override mid-job. Best-effort: a resolver failure logs but
        # does not abort the submit (no overrides → empty catalogue,
        # which is fine).
        try:
            from dashboard.specialist.resolver import resolve

            catalogue = resolve(project)
            atomic_write_json(job_dir / "specialist-catalogue.json", catalogue)
        except Exception as exc:
            log.warning(
                "could not resolve specialist catalogue for %s: %s",
                project.slug,
                exc,
            )
    except OSError as e:
        return [CompileFailure("io", None, f"could not write job dir {job_dir}: {e}")]

    return CompileSuccess(
        job_slug=job_slug,
        job_dir=job_dir,
        job_config=job_config,
        stages=stages,
        dry_run=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_project(slug: str, *, root: Path | None) -> ProjectConfig | None:
    p = paths.project_json(slug, root=root)
    if not p.exists():
        return None
    try:
        return ProjectConfig.model_validate_json(p.read_text())
    except (ValidationError, OSError):
        return None


def _resolve_template(
    job_type: str,
    *,
    templates_dir: Path | None,
    root: Path | None,
) -> Path | None:
    """Find the global job template file. Order:

    1. *templates_dir* (if explicitly passed by tests).
    2. ``<root>/job-templates/<job_type>.yaml``  (user's hammock root).
    3. Bundled location shipped with the package.
    """
    if templates_dir is not None:
        candidate = templates_dir / f"{job_type}.yaml"
        if candidate.exists():
            return candidate
        return None

    user_dir = paths.job_templates_dir(root)
    user_candidate = user_dir / f"{job_type}.yaml"
    if user_candidate.exists():
        return user_candidate

    bundled = _BUNDLED_TEMPLATES_DIR / f"{job_type}.yaml"
    if bundled.exists():
        return bundled
    return None


def _project_override_path(project: ProjectConfig, job_type: str) -> Path | None:
    repo = Path(project.repo_path)
    return paths.project_overrides_root(repo) / "job-template-overrides" / f"{job_type}.yaml"


def _generate_job_slug(
    title: str,
    *,
    root: Path | None,
    now: datetime,
) -> str:
    """Generate ``YYYY-MM-DD-<title-slug>`` with collision suffix on conflict."""
    title_slug = derive_slug(title)  # may raise SlugDerivationError
    date = now.strftime("%Y-%m-%d")
    base = f"{date}-{title_slug}"
    candidate = base
    suffix = 2
    while paths.job_dir(candidate, root=root).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


# ---------------------------------------------------------------------------
# Param binding
# ---------------------------------------------------------------------------


_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")


def _bind_params(
    template: dict[str, Any],
    context: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Walk the template, substituting ``${dotted.path}`` against context.

    Returns ``(bound_template, errors)``. Unknown placeholders are reported.
    """
    errors: list[str] = []

    def visit(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: visit(v) for k, v in node.items()}
        if isinstance(node, list):
            return [visit(item) for item in node]
        if isinstance(node, str):
            return _substitute(node, context, errors)
        return node

    return visit(template), errors


def _substitute(s: str, context: dict[str, Any], errors: list[str]) -> str:
    def repl(m: re.Match[str]) -> str:
        path = m.group(1).strip()
        cur: Any = context
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                errors.append(f"unknown placeholder ${{{path}}}")
                return m.group(0)
        return str(cur)

    return _PLACEHOLDER.sub(repl, s)


# ---------------------------------------------------------------------------
# Failure adapters
# ---------------------------------------------------------------------------


def _override_to_compile(f: OverrideFailure) -> CompileFailure:
    return CompileFailure(kind=f"override:{f.kind}", stage_id=f.stage_id, message=f.message)


def _validation_to_compile(f: ValidationFailure) -> CompileFailure:
    return CompileFailure(kind=f"validation:{f.rule}", stage_id=f.stage_id, message=f.message)
