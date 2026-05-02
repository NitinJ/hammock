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
    failures.extend(validate_parallel_with(stages))
    failures.extend(validate_predicates(stages))
    failures.extend(validate_human_stages_have_presentation(stages))
    failures.extend(validate_no_path_traversal(stages))
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


def validate_parallel_with(stages: list[StageDefinition]) -> list[ValidationFailure]:
    """Every ``parallel_with`` reference is symmetric and references existing ids."""
    failures: list[ValidationFailure] = []
    by_id = {s.id: s for s in stages}
    for s in stages:
        if not s.parallel_with:
            continue
        for ref in s.parallel_with:
            if ref not in by_id:
                failures.append(
                    ValidationFailure(
                        "parallel_with",
                        s.id,
                        f"parallel_with references unknown stage id {ref!r}",
                    )
                )
                continue
            other = by_id[ref]
            if not other.parallel_with or s.id not in other.parallel_with:
                failures.append(
                    ValidationFailure(
                        "parallel_with",
                        s.id,
                        f"parallel_with relation is asymmetric: {s.id!r} references "
                        f"{ref!r} but {ref!r} does not reference back",
                    )
                )
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
    return failures


def _path_unsafe(p: str) -> bool:
    if p.startswith("/"):
        return True
    parts = PurePosixPath(p).parts
    return ".." in parts
