"""Validation rules for compiled job templates.

Per design doc § Plan Compiler § Validation rules. Run after Pydantic
validation has succeeded — these are structural / cross-stage constraints
that aren't expressible in a single model.

Each validator returns a list of ``ValidationFailure`` describing every
violation it found; the compiler aggregates them and surfaces all errors at
once rather than failing on the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from shared.models import StageDefinition
from shared.predicate import PredicateError, parse_predicate

# The set of inputs every job has at runtime by virtue of being a job. The
# compiler writes ``prompt.md``; everything else flows from stage outputs.
JOB_LEVEL_INPUTS: frozenset[str] = frozenset({"prompt.md"})


@dataclass(frozen=True)
class ValidationFailure:
    """A single validation violation."""

    rule: str
    stage_id: str | None
    message: str


def validate_plan(stages: list[StageDefinition]) -> list[ValidationFailure]:
    """Run every validation rule; return aggregated failures."""
    failures: list[ValidationFailure] = []
    failures.extend(validate_unique_ids(stages))
    failures.extend(validate_dag_closure(stages))
    failures.extend(validate_loop_back_targets(stages))
    failures.extend(validate_predicates(stages))
    failures.extend(validate_human_stages_have_presentation(stages))
    failures.extend(validate_no_path_traversal(stages))
    failures.extend(validate_known_validators(stages))
    return failures


def validate_unique_ids(stages: list[StageDefinition]) -> list[ValidationFailure]:
    seen: dict[str, int] = {}
    failures: list[ValidationFailure] = []
    for i, s in enumerate(stages):
        if s.id in seen:
            failures.append(
                ValidationFailure(
                    "unique_ids",
                    s.id,
                    f"duplicate stage id {s.id!r} (also at position {seen[s.id]})",
                )
            )
        else:
            seen[s.id] = i
    return failures


def validate_dag_closure(stages: list[StageDefinition]) -> list[ValidationFailure]:
    """Every required input must come from a prior stage's outputs or be job-level."""
    available: set[str] = set(JOB_LEVEL_INPUTS)
    failures: list[ValidationFailure] = []
    for s in stages:
        for path in s.inputs.required:
            if path not in available:
                failures.append(
                    ValidationFailure(
                        "dag_closure",
                        s.id,
                        f"required input {path!r} is not produced by any prior stage "
                        f"and is not a job-level input ({sorted(JOB_LEVEL_INPUTS)})",
                    )
                )
        # Always advance available with this stage's outputs, even if the
        # stage failed validation — produces fewer cascading errors.
        for path in s.outputs.required:
            available.add(path)
    return failures


def validate_loop_back_targets(stages: list[StageDefinition]) -> list[ValidationFailure]:
    """``loop_back.to`` must reference a stage id that appears earlier in the list."""
    failures: list[ValidationFailure] = []
    seen_before: set[str] = set()
    for s in stages:
        if s.loop_back is not None:
            target = s.loop_back.to
            if target == s.id:
                failures.append(
                    ValidationFailure(
                        "loop_back",
                        s.id,
                        f"loop_back.to points at the loop-back stage itself ({target!r})",
                    )
                )
            elif target not in seen_before:
                failures.append(
                    ValidationFailure(
                        "loop_back",
                        s.id,
                        f"loop_back.to {target!r} is not an earlier stage in the plan",
                    )
                )
        seen_before.add(s.id)
    return failures


def validate_predicates(stages: list[StageDefinition]) -> list[ValidationFailure]:
    """``runs_if`` and ``loop_back.condition`` must parse against the grammar."""
    failures: list[ValidationFailure] = []
    for s in stages:
        if s.runs_if is not None:
            try:
                parse_predicate(s.runs_if)
            except PredicateError as e:
                failures.append(
                    ValidationFailure("predicate", s.id, f"runs_if does not parse: {e}")
                )
        if s.loop_back is not None:
            try:
                parse_predicate(s.loop_back.condition)
            except PredicateError as e:
                failures.append(
                    ValidationFailure("predicate", s.id, f"loop_back.condition does not parse: {e}")
                )
    return failures


def validate_human_stages_have_presentation(
    stages: list[StageDefinition],
) -> list[ValidationFailure]:
    """Every ``worker: human`` stage needs a ``presentation`` block (UI form)."""
    failures: list[ValidationFailure] = []
    for s in stages:
        if s.worker == "human" and s.presentation is None:
            failures.append(
                ValidationFailure(
                    "presentation",
                    s.id,
                    "human-worker stage missing 'presentation' block; the dashboard "
                    "cannot render a form for it",
                )
            )
    return failures


def validate_no_path_traversal(stages: list[StageDefinition]) -> list[ValidationFailure]:
    """Input/output paths must be relative and must not contain ``..``."""
    failures: list[ValidationFailure] = []
    for s in stages:
        for kind, paths_iter in (
            ("inputs.required", s.inputs.required),
            ("inputs.optional", s.inputs.optional or []),
            ("outputs.required", s.outputs.required),
        ):
            for p in paths_iter:
                if _path_unsafe(p):
                    failures.append(
                        ValidationFailure(
                            "path_traversal",
                            s.id,
                            f"{kind} path {p!r} is unsafe (absolute or contains '..')",
                        )
                    )
        for ro in s.exit_condition.required_outputs or []:
            if _path_unsafe(ro.path):
                failures.append(
                    ValidationFailure(
                        "path_traversal",
                        s.id,
                        f"exit_condition.required_outputs path {ro.path!r} is unsafe "
                        "(absolute or contains '..')",
                    )
                )
        for av in s.exit_condition.artifact_validators or []:
            if _path_unsafe(av.path):
                failures.append(
                    ValidationFailure(
                        "path_traversal",
                        s.id,
                        f"exit_condition.artifact_validators path {av.path!r} is unsafe "
                        "(absolute or contains '..')",
                    )
                )
    return failures


def _path_unsafe(p: str) -> bool:
    if p.startswith("/"):
        return True
    parts = PurePosixPath(p).parts
    return ".." in parts


def validate_known_validators(stages: list[StageDefinition]) -> list[ValidationFailure]:
    """Every validator name in ``required_outputs[*].validators`` and
    ``artifact_validators[*].schema`` must be registered in the artifact
    validator registry.  Fail-closed: unknown names are a compile-time error.
    """
    from shared.artifact_validators import REGISTRY

    failures: list[ValidationFailure] = []
    for s in stages:
        ec = s.exit_condition
        for ro in ec.required_outputs or []:
            for name in ro.validators or []:
                if name not in REGISTRY:
                    failures.append(
                        ValidationFailure(
                            "known_validators",
                            s.id,
                            f"unknown validator {name!r}; registered names: {sorted(REGISTRY)}",
                        )
                    )
        for av in ec.artifact_validators or []:
            if av.schema_ not in REGISTRY:
                failures.append(
                    ValidationFailure(
                        "known_validators",
                        s.id,
                        f"unknown artifact_validator schema {av.schema_!r}; "
                        f"registered names: {sorted(REGISTRY)}",
                    )
                )
    return failures
