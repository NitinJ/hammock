"""`design-spec` variable type — design document produced by the agent."""

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


class DesignSpecDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DesignSpecValue(BaseModel):
    """A simple structured design doc. Concrete, minimal."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1)
    overview: str = Field(..., min_length=1)
    proposed_changes: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)


_PROMPT_HINT = """\
Strict JSON. Allowed fields ONLY:

- `title`: short title string (required, non-empty).
- `overview`: 2-5 sentence description (required, non-empty).
- `proposed_changes`: list of strings, each describing one change.
- `risks`: list of strings, each describing one risk.
- `out_of_scope`: list of strings.

Schema uses extra='forbid'. Do NOT add fields like `author`, `date`,
`alternatives_considered`.\
"""


class DesignSpecType:
    name: ClassVar[str] = "design-spec"
    Decl: ClassVar[type[DesignSpecDecl]] = DesignSpecDecl
    Value: ClassVar[type[DesignSpecValue]] = DesignSpecValue

    def produce(self, decl: DesignSpecDecl, ctx: NodeContext) -> DesignSpecValue:
        path = ctx.expected_path()
        if not path.is_file():
            raise VariableTypeError(f"design-spec not produced at {path}")
        raw = path.read_bytes()
        if not raw.strip():
            raise VariableTypeError(f"design-spec at {path} is empty")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VariableTypeError(f"design-spec at {path} is not valid JSON: {exc}") from exc
        try:
            return DesignSpecValue.model_validate(data)
        except ValidationError as exc:
            raise VariableTypeError(f"design-spec schema invalid: {exc}") from exc

    def render_for_producer(self, decl: DesignSpecDecl, ctx: PromptContext) -> str:
        return (
            f"### Output `{ctx.var_name}` (design-spec)\n\n"
            f"Write your output as JSON to: `{ctx.expected_path()}`.\n\n"
            f"{_PROMPT_HINT}\n"
        )

    def render_for_consumer(
        self, decl: DesignSpecDecl, value: DesignSpecValue, ctx: PromptContext
    ) -> str:
        lines = [
            f"### Input `{ctx.var_name}` (design-spec)",
            "",
            f"**Title:** {value.title}",
            "",
            f"**Overview:** {value.overview}",
        ]
        if value.proposed_changes:
            lines.append("\n**Proposed changes:**")
            for ch in value.proposed_changes:
                lines.append(f"  - {ch}")
        if value.risks:
            lines.append("\n**Risks:**")
            for r in value.risks:
                lines.append(f"  - {r}")
        if value.out_of_scope:
            lines.append("\n**Out of scope:**")
            for x in value.out_of_scope:
                lines.append(f"  - {x}")
        return "\n".join(lines)

    def form_schema(self, decl: DesignSpecDecl) -> FormSchema | None:
        return None
