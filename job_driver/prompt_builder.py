"""Build the structured prompt RealStageRunner hands to ``claude -p``.

Per real-claude e2e precondition track P2: agents need the job's
overall prompt, declared inputs + outputs, and the working directory —
not the one-line stage description that previous code used. This
module produces a single string with stable section headings so the
agent (and tests) can rely on the shape.

The function is pure aside from reading declared input files; nothing
is written.
"""

from __future__ import annotations

from pathlib import Path

from shared.models.stage import (
    ArtifactValidator,
    RequiredOutput,
    StageDefinition,
)

DEFAULT_MAX_INPUT_BYTES: int = 16 * 1024
# Aggregate cap across all inlined input bodies (codex review on PR #26):
# the per-file cap doesn't help when many inputs are declared. When the
# total exceeds this budget the remaining inputs are listed by path
# only with a "(omitted: prompt size cap)" notice so the agent still
# sees the names and can read the files itself if needed.
DEFAULT_MAX_TOTAL_INPUT_BYTES: int = 64 * 1024


def build_stage_prompt(
    stage_def: StageDefinition,
    *,
    job_prompt: str,
    job_dir: Path,
    cwd: Path,
    max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
    max_total_input_bytes: int = DEFAULT_MAX_TOTAL_INPUT_BYTES,
) -> str:
    """Render the stage prompt as one string.

    Section order (each absent if its source has no content):

    1. ``# Stage: <id>`` + description (or fallback to id)
    2. ``## Job context`` — verbatim ``job_prompt``
    3. ``## Required inputs`` — for each input declared on the stage,
       a sub-heading + the file content (truncated at ``max_input_bytes``).
       Missing files get a "not found" note rather than failing.
       Inputs are resolved against ``job_dir``; paths that escape it
       are flagged, never read.
    4. ``## Optional inputs`` — same shape, only when declared.
    5. ``## Required outputs`` — paths from
       ``stage_def.exit_condition.required_outputs``, annotated with
       the validator schema if one is registered.
    6. ``## Working directory`` — absolute ``cwd`` plus a one-line
       reminder.

    All inputs are read by this function; the caller doesn't need to
    pre-load them.
    """
    description = (stage_def.description or "").strip() or stage_def.id
    parts: list[str] = [
        f"# Stage: {stage_def.id}",
        "",
        description,
        "",
        "## Job context",
        "",
        job_prompt.strip(),
    ]

    # Aggregate budget tracker: shared across required + optional sections
    # so a long required input doesn't get inlined while optional inputs
    # silently reach the cap. The mutable list is convention for "the
    # remaining budget"; threaded through both render calls.
    remaining_budget = [max_total_input_bytes]

    required_inputs = list(stage_def.inputs.required)
    if required_inputs:
        parts.extend(["", "## Required inputs"])
        parts.extend(_render_inputs(required_inputs, job_dir, max_input_bytes, remaining_budget))

    optional_inputs = list(stage_def.inputs.optional or [])
    if optional_inputs:
        parts.extend(["", "## Optional inputs"])
        parts.extend(_render_inputs(optional_inputs, job_dir, max_input_bytes, remaining_budget))

    outputs = stage_def.exit_condition.required_outputs or []
    if outputs:
        parts.extend(["", "## Required outputs", ""])
        parts.extend(_render_outputs(outputs, stage_def.exit_condition.artifact_validators))

    parts.extend(
        [
            "",
            "## Working directory",
            "",
            str(cwd),
            "",
            "Write outputs to paths relative to the working directory unless the",
            "contract says otherwise.",
            "",
        ]
    )

    return "\n".join(parts)


def _render_inputs(
    paths: list[str],
    job_dir: Path,
    max_bytes: int,
    remaining_budget: list[int],
) -> list[str]:
    """Render input file sections, respecting per-file + aggregate caps.

    ``remaining_budget`` is a single-element list used as a mutable
    cell threaded across multiple ``_render_inputs`` calls so the
    required + optional sections share one aggregate budget.
    """
    out: list[str] = []
    job_dir_resolved = job_dir.resolve()
    for relpath in paths:
        out.extend(["", f"### {relpath}", ""])
        candidate = (job_dir / relpath).resolve()
        try:
            candidate.relative_to(job_dir_resolved)
        except ValueError:
            out.append(f"(path {relpath!r} is outside job dir; not read)")
            continue
        if not candidate.is_file():
            out.append(f"(file {relpath!r} not found at {candidate})")
            continue
        if remaining_budget[0] <= 0:
            out.append("(omitted: prompt size cap reached; agent should read this directly)")
            continue
        # Read at most one extra byte over the per-file cap so we can
        # detect truncation without slurping the whole file into memory
        # for large declared inputs.
        per_file_cap = min(max_bytes, remaining_budget[0])
        try:
            with candidate.open("rb") as fh:
                head = fh.read(per_file_cap + 1)
        except OSError as exc:
            out.append(f"(could not read {relpath!r}: {exc})")
            continue
        if len(head) > per_file_cap:
            body = head[:per_file_cap]
            out.append(body.decode("utf-8", errors="replace"))
            out.append("[truncated]")
            consumed = per_file_cap
        else:
            out.append(head.decode("utf-8", errors="replace"))
            consumed = len(head)
        remaining_budget[0] -= consumed
    return out


def _render_outputs(
    outputs: list[RequiredOutput],
    validators: list[ArtifactValidator] | None,
) -> list[str]:
    schema_by_path: dict[str, str] = {}
    for v in validators or []:
        schema_by_path[v.path] = v.schema_
    lines: list[str] = []
    for out in outputs:
        schema = schema_by_path.get(out.path)
        if schema:
            lines.append(f"- {out.path} (validated by: {schema})")
        else:
            lines.append(f"- {out.path}")
    return lines
