"""`summary` variable type — final job summary produced by write-summary."""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.v1.types.protocol import (
    FormSchema,
    NodeContext,
    PromptContext,
    VariableTypeError,
)


class SummaryDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SummaryValue(BaseModel):
    """Final job summary — Stage 2 carries a ``document`` markdown field."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1)
    pr_urls: list[str] = Field(default_factory=list)
    document: str = Field(..., min_length=1)
    """Full summary in markdown — primary view in the dashboard."""


_PROMPT_HINT = """\
Strict JSON. Allowed fields ONLY:

- `text`: 2-6 sentence narrative (required, non-empty).
- `pr_urls`: list of strings — every PR URL the workflow produced.
- `document`: full summary as markdown (required, non-empty). Place the
  narrative content here — the dashboard renders this as the primary
  view.

Schema uses extra='forbid'.\
"""


class SummaryType:
    name: ClassVar[str] = "summary"
    Decl: ClassVar[type[SummaryDecl]] = SummaryDecl
    Value: ClassVar[type[SummaryValue]] = SummaryValue

    def produce(self, decl: SummaryDecl, ctx: NodeContext) -> SummaryValue:
        path = ctx.attempt_output_path()
        if not path.is_file():
            raise VariableTypeError(f"summary not produced at {path}")
        raw = path.read_bytes()
        if not raw.strip():
            raise VariableTypeError(f"summary at {path} is empty")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VariableTypeError(f"summary at {path} is not valid JSON: {exc}") from exc
        try:
            return SummaryValue.model_validate(data)
        except ValidationError as exc:
            raise VariableTypeError(f"summary schema invalid: {exc}") from exc

    def render_for_producer(self, decl: SummaryDecl, ctx: PromptContext) -> str:
        return (
            f"### Output `{ctx.var_name}` (summary)\n\n"
            f"Write your output as JSON to: `{ctx.attempt_output_path()}`.\n\n"
            f"{_PROMPT_HINT}\n"
        )

    def render_for_consumer(
        self, decl: SummaryDecl, value: SummaryValue, ctx: PromptContext
    ) -> str:
        lines = [
            f"### Input `{ctx.var_name}` (summary)",
            "",
            value.text,
        ]
        if value.pr_urls:
            lines.append("\n**PRs:**")
            for u in value.pr_urls:
                lines.append(f"  - {u}")
        lines.append("")
        lines.append("#### Document")
        lines.append("")
        lines.append(value.document)
        return "\n".join(lines)

    def form_schema(self, decl: SummaryDecl) -> FormSchema | None:
        return None
