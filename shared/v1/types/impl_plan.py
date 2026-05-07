"""`impl-plan` variable type — implementation plan with stage count.

Per design-patch §6.2 + §6.4: drives a count loop via field access on
``$impl-plan-loop.impl_plan[last].count``. Carries both the stage count
and a per-stage description list so each iteration of implement-loop
can read its corresponding stage entry.

Engine support: ``count`` is the canonical field for the count-loop's
``count: $ref.count`` form. The Pydantic model exposes it as a regular
int field; ``predicate._walk_field_path`` walks Pydantic models, so
field access lands here for free.
"""

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


class ImplPlanDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImplPlanStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)


class ImplPlanValue(BaseModel):
    """Implementation plan — Stage 2 carries a ``document`` markdown field."""

    model_config = ConfigDict(extra="forbid")

    count: int = Field(..., ge=0)
    """Number of implement-loop iterations this plan calls for. Engine
    reads this via ``$impl-plan-loop.impl_plan[last].count`` to size the
    count loop."""

    stages: list[ImplPlanStage] = Field(default_factory=list)
    """Per-stage description; ``len(stages) == count`` is the convention
    but not enforced — the count is what drives iteration."""

    document: str = Field(..., min_length=1)
    """Full impl plan in markdown — primary view in the dashboard."""


_PROMPT_HINT = """\
Strict JSON. Allowed fields ONLY:

- `count`: non-negative int — how many implement iterations are needed.
- `stages`: list of `{name: string, description: string}` objects.
  Convention: `len(stages) == count`.
- `document`: full impl plan as markdown (required, non-empty). Place
  the narrative content here — the dashboard renders this as the
  primary view and downstream agents consume it directly.

Schema uses extra='forbid'.\
"""


class ImplPlanType:
    name: ClassVar[str] = "impl-plan"
    Decl: ClassVar[type[ImplPlanDecl]] = ImplPlanDecl
    Value: ClassVar[type[ImplPlanValue]] = ImplPlanValue

    def produce(self, decl: ImplPlanDecl, ctx: NodeContext) -> ImplPlanValue:
        path = ctx.expected_path()
        if not path.is_file():
            raise VariableTypeError(f"impl-plan not produced at {path}")
        raw = path.read_bytes()
        if not raw.strip():
            raise VariableTypeError(f"impl-plan at {path} is empty")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VariableTypeError(f"impl-plan at {path} is not valid JSON: {exc}") from exc
        try:
            return ImplPlanValue.model_validate(data)
        except ValidationError as exc:
            raise VariableTypeError(f"impl-plan schema invalid: {exc}") from exc

    def render_for_producer(self, decl: ImplPlanDecl, ctx: PromptContext) -> str:
        return (
            f"### Output `{ctx.var_name}` (impl-plan)\n\n"
            f"Write your output as JSON to: `{ctx.expected_path()}`.\n\n"
            f"{_PROMPT_HINT}\n"
        )

    def render_for_consumer(
        self, decl: ImplPlanDecl, value: ImplPlanValue, ctx: PromptContext
    ) -> str:
        lines = [
            f"### Input `{ctx.var_name}` (impl-plan)",
            "",
            f"**Count:** {value.count}",
        ]
        if value.stages:
            lines.append("\n**Stages:**")
            for k, s in enumerate(value.stages):
                lines.append(f"  {k}. **{s.name}** — {s.description}")
        lines.append("")
        lines.append("#### Document")
        lines.append("")
        lines.append(value.document)
        return "\n".join(lines)

    def form_schema(self, decl: ImplPlanDecl) -> FormSchema | None:
        return None
