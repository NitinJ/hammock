"""`bug-report` variable type — structured bug description produced by the
write-bug-report agent node."""

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


class BugReportDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BugReportValue(BaseModel):
    """Structured bug report. Concrete and minimal — just enough fields
    for downstream nodes (design-spec writer, reviewer) to operate from.

    Per Stage 2 of ``docs/hammock-workflow.md``: bug-report carries a
    ``document`` field of markdown alongside the structured fields. The
    dashboard renders ``document`` as the primary view; downstream
    agents read both the structured fields and the prose body."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=1)
    """One-paragraph summary of what's wrong."""

    repro_steps: list[str] = Field(default_factory=list)
    """Ordered repro steps; empty list if not applicable."""

    expected_behaviour: str | None = None
    """What should happen instead."""

    actual_behaviour: str | None = None
    """What happens currently (the bug)."""

    document: str = Field(..., min_length=1)
    """Full bug report in markdown — narrative the dashboard renders as
    the primary view and downstream agents consume directly."""


_PROMPT_HINT = """\
Strict JSON. Allowed fields ONLY:

- `summary`: 1-paragraph string (required, non-empty).
- `repro_steps`: list of strings (each one ordered repro step).
- `expected_behaviour`: string or null.
- `actual_behaviour`: string or null.
- `document`: full bug report as markdown (required, non-empty). Place
  the narrative content here — the dashboard renders this as the
  primary view and downstream agents consume it directly.

Do NOT add other fields like `severity`, `assignee`, `notes` — the schema
uses extra='forbid' and they will be rejected.\
"""


class BugReportType:
    name: ClassVar[str] = "bug-report"
    Decl: ClassVar[type[BugReportDecl]] = BugReportDecl
    Value: ClassVar[type[BugReportValue]] = BugReportValue

    def produce(self, decl: BugReportDecl, ctx: NodeContext) -> BugReportValue:
        path = ctx.expected_path()
        if not path.is_file():
            raise VariableTypeError(
                f"bug-report not produced at {path} — agent must write JSON here"
            )
        raw = path.read_bytes()
        if not raw.strip():
            raise VariableTypeError(f"bug-report at {path} is empty")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VariableTypeError(f"bug-report at {path} is not valid JSON: {exc}") from exc
        try:
            return BugReportValue.model_validate(data)
        except ValidationError as exc:
            raise VariableTypeError(f"bug-report schema invalid: {exc}") from exc

    def render_for_producer(self, decl: BugReportDecl, ctx: PromptContext) -> str:
        return (
            f"### Output `{ctx.var_name}` (bug-report)\n\n"
            f"Write your output as JSON to: `{ctx.expected_path()}`.\n\n"
            f"{_PROMPT_HINT}\n"
        )

    def render_for_consumer(
        self, decl: BugReportDecl, value: BugReportValue, ctx: PromptContext
    ) -> str:
        lines = [
            f"### Input `{ctx.var_name}` (bug-report)",
            "",
            f"**Summary:** {value.summary}",
        ]
        if value.actual_behaviour:
            lines.append(f"**Actual:** {value.actual_behaviour}")
        if value.expected_behaviour:
            lines.append(f"**Expected:** {value.expected_behaviour}")
        if value.repro_steps:
            lines.append("**Repro steps:**")
            for i, step in enumerate(value.repro_steps, 1):
                lines.append(f"  {i}. {step}")
        lines.append("")
        lines.append("#### Document")
        lines.append("")
        lines.append(value.document)
        return "\n".join(lines)

    def form_schema(self, decl: BugReportDecl) -> FormSchema | None:
        # Not human-producible in v1 (agent only writes bug reports).
        return None
