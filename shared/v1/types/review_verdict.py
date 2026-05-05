"""`review-verdict` variable type — produced by reviewer nodes (agent or
human). Carries a verdict enum + a short summary + a list of unresolved
concerns. Used both for spec/plan reviews and for PR review/merge gates."""

from __future__ import annotations

import json
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.v1.types.protocol import (
    FormSchema,
    NodeContext,
    PromptContext,
    VariableTypeError,
)

# Verdicts used in v1. The same type is reused across spec reviews and PR
# review-and-merge gates; the loop predicate decides which verdicts mean
# "exit" vs "iterate again".
VerdictLiteral = Literal["approved", "needs-revision", "rejected", "merged"]


class Concern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["blocker", "major", "minor"]
    concern: str = Field(..., min_length=1)
    location: str | None = None


class ReviewVerdictDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # No per-variable config in v1.


class ReviewVerdictValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: VerdictLiteral
    summary: str = Field(..., min_length=1)
    """1-3 sentence human-readable summary of the verdict."""

    unresolved_concerns: list[Concern] = Field(default_factory=list)
    """Empty when verdict == 'approved' or 'merged'."""

    addressed_in_this_iteration: list[str] = Field(default_factory=list)
    """Concrete things this iteration addressed (informational; empty on
    iteration 1)."""


_PROMPT_HINT = """\
Strict JSON. Allowed fields ONLY:

- `verdict`: one of 'approved' | 'needs-revision' | 'rejected' | 'merged'.
- `summary`: 1-3 sentence string (required, non-empty).
- `unresolved_concerns`: list of objects, each with
  `{severity: 'blocker'|'major'|'minor', concern: string, location: string|null}`.
  Empty list when verdict is 'approved' or 'merged'.
- `addressed_in_this_iteration`: list of strings (empty on iteration 1).

Schema uses extra='forbid'. Do NOT add fields like 'reviewer', 'strengths',
'minor_suggestions', 'approval_conditions', 'reviewed_artifact'.\
"""


class ReviewVerdictType:
    name: ClassVar[str] = "review-verdict"
    Decl: ClassVar[type[ReviewVerdictDecl]] = ReviewVerdictDecl
    Value: ClassVar[type[ReviewVerdictValue]] = ReviewVerdictValue

    def produce(self, decl: ReviewVerdictDecl, ctx: NodeContext) -> ReviewVerdictValue:
        path = ctx.expected_path()
        if not path.is_file():
            raise VariableTypeError(
                f"review-verdict not produced at {path}"
            )
        raw = path.read_bytes()
        if not raw.strip():
            raise VariableTypeError(f"review-verdict at {path} is empty")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VariableTypeError(
                f"review-verdict at {path} is not valid JSON: {exc}"
            ) from exc
        try:
            return ReviewVerdictValue.model_validate(data)
        except ValidationError as exc:
            raise VariableTypeError(f"review-verdict schema invalid: {exc}") from exc

    def render_for_producer(
        self, decl: ReviewVerdictDecl, ctx: PromptContext
    ) -> str:
        return (
            f"### Output `{ctx.var_name}` (review-verdict)\n\n"
            f"Write your output as JSON to: `{ctx.expected_path()}`.\n\n"
            f"{_PROMPT_HINT}\n"
        )

    def render_for_consumer(
        self, decl: ReviewVerdictDecl, value: ReviewVerdictValue, ctx: PromptContext
    ) -> str:
        lines = [
            f"### Input `{ctx.var_name}` (review-verdict)",
            "",
            f"**Verdict:** {value.verdict}",
            f"**Summary:** {value.summary}",
        ]
        if value.unresolved_concerns:
            lines.append("\n**Unresolved concerns:**")
            for c in value.unresolved_concerns:
                loc = f" — {c.location}" if c.location else ""
                lines.append(f"  - [{c.severity}] {c.concern}{loc}")
        return "\n".join(lines)

    def form_schema(self, decl: ReviewVerdictDecl) -> FormSchema | None:
        return FormSchema(
            fields=[
                ("verdict", "select:approved,needs-revision,rejected,merged"),
                ("summary", "textarea"),
                ("unresolved_concerns", "list[concern]"),
            ]
        )
