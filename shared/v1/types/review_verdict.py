"""``review-verdict`` variable type — produced by reviewer nodes (agent or
human). Carries a verdict enum + a short summary.

Per design-patch §9.4 simplification:
- Verdicts: approved | needs-revision | rejected. The "merged" verdict
  moved to the new ``pr-review-verdict`` type, which owns PR-merge HIL.
- The ``Concern`` sub-model + ``unresolved_concerns`` and
  ``addressed_in_this_iteration`` fields are removed. Reviewer prose
  goes into ``summary`` directly.
"""

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

VerdictLiteral = Literal["approved", "needs-revision", "rejected"]


class ReviewVerdictDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # No per-variable config in v1.


class ReviewVerdictValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: VerdictLiteral
    summary: str = Field(..., min_length=1)
    """1-3 sentence human-readable summary of the verdict."""

    document: str = Field(..., min_length=1)
    """Markdown narrative explaining the review reasoning: what was
    reviewed, what was noted, and why this verdict was reached. The
    dashboard renders this as the primary reviewer-facing view; the
    next iteration's writer agent consumes it directly so concerns
    have a place to live beyond the 1-3 sentence summary."""


_PROMPT_HINT = """\
Strict JSON. Allowed fields ONLY:

- `verdict`: one of 'approved' | 'needs-revision' | 'rejected'.
- `summary`: 1-3 sentence string (required, non-empty).
- `document`: full review as markdown (required, non-empty). Write the
  narrative explaining your reasoning here: what you reviewed, what
  you noted (concerns, strengths, missing context), and why you
  reached this verdict. The next iteration's writer agent reads this
  directly to address your feedback.

Schema uses extra='forbid'. Do NOT add fields like 'reviewer',
'strengths', 'minor_suggestions', 'approval_conditions', 'unresolved_concerns',
'addressed_in_this_iteration', or 'reviewed_artifact'.\
"""


class ReviewVerdictType:
    name: ClassVar[str] = "review-verdict"
    Decl: ClassVar[type[ReviewVerdictDecl]] = ReviewVerdictDecl
    Value: ClassVar[type[ReviewVerdictValue]] = ReviewVerdictValue

    def produce(self, decl: ReviewVerdictDecl, ctx: NodeContext) -> ReviewVerdictValue:
        path = ctx.attempt_output_path()
        if not path.is_file():
            raise VariableTypeError(f"review-verdict not produced at {path}")
        raw = path.read_bytes()
        if not raw.strip():
            raise VariableTypeError(f"review-verdict at {path} is empty")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VariableTypeError(f"review-verdict at {path} is not valid JSON: {exc}") from exc
        try:
            return ReviewVerdictValue.model_validate(data)
        except ValidationError as exc:
            raise VariableTypeError(f"review-verdict schema invalid: {exc}") from exc

    def render_for_producer(self, decl: ReviewVerdictDecl, ctx: PromptContext) -> str:
        return (
            f"### Output `{ctx.var_name}` (review-verdict)\n\n"
            f"Write your output as JSON to: `{ctx.attempt_output_path()}`.\n\n"
            f"{_PROMPT_HINT}\n"
        )

    def render_for_consumer(
        self, decl: ReviewVerdictDecl, value: ReviewVerdictValue, ctx: PromptContext
    ) -> str:
        return (
            f"### Input `{ctx.var_name}` (review-verdict)\n"
            f"\n"
            f"**Verdict:** {value.verdict}\n"
            f"**Summary:** {value.summary}\n"
            f"\n"
            f"#### Document\n"
            f"\n"
            f"{value.document}"
        )

    def form_schema(self, decl: ReviewVerdictDecl) -> FormSchema | None:
        return FormSchema(
            fields=[
                ("verdict", "select:approved,needs-revision,rejected"),
                ("summary", "textarea"),
                ("document", "textarea"),
            ]
        )
