"""`impl-spec` variable type — implementation specification.

Per design-patch §6.2: derived from the design spec, the impl spec
captures *how* the change will be implemented (architecture, components
touched, data flow). Same shape pattern as design-spec.
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


class ImplSpecDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImplSpecValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1)
    overview: str = Field(..., min_length=1)
    components: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)


_PROMPT_HINT = """\
Strict JSON. Allowed fields ONLY:

- `title`: short title string (required, non-empty).
- `overview`: 2-5 sentence implementation summary (required, non-empty).
- `components`: list of strings (files / modules / classes touched).
- `interfaces`: list of strings (function signatures or API shapes).
- `edge_cases`: list of strings.

Schema uses extra='forbid'.\
"""


class ImplSpecType:
    name: ClassVar[str] = "impl-spec"
    Decl: ClassVar[type[ImplSpecDecl]] = ImplSpecDecl
    Value: ClassVar[type[ImplSpecValue]] = ImplSpecValue

    def produce(self, decl: ImplSpecDecl, ctx: NodeContext) -> ImplSpecValue:
        path = ctx.expected_path()
        if not path.is_file():
            raise VariableTypeError(f"impl-spec not produced at {path}")
        raw = path.read_bytes()
        if not raw.strip():
            raise VariableTypeError(f"impl-spec at {path} is empty")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VariableTypeError(
                f"impl-spec at {path} is not valid JSON: {exc}"
            ) from exc
        try:
            return ImplSpecValue.model_validate(data)
        except ValidationError as exc:
            raise VariableTypeError(f"impl-spec schema invalid: {exc}") from exc

    def render_for_producer(self, decl: ImplSpecDecl, ctx: PromptContext) -> str:
        return (
            f"### Output `{ctx.var_name}` (impl-spec)\n\n"
            f"Write your output as JSON to: `{ctx.expected_path()}`.\n\n"
            f"{_PROMPT_HINT}\n"
        )

    def render_for_consumer(
        self, decl: ImplSpecDecl, value: ImplSpecValue, ctx: PromptContext
    ) -> str:
        lines = [
            f"### Input `{ctx.var_name}` (impl-spec)",
            "",
            f"**Title:** {value.title}",
            "",
            f"**Overview:** {value.overview}",
        ]
        if value.components:
            lines.append("\n**Components:**")
            for c in value.components:
                lines.append(f"  - {c}")
        if value.interfaces:
            lines.append("\n**Interfaces:**")
            for i in value.interfaces:
                lines.append(f"  - {i}")
        if value.edge_cases:
            lines.append("\n**Edge cases:**")
            for e in value.edge_cases:
                lines.append(f"  - {e}")
        return "\n".join(lines)

    def form_schema(self, decl: ImplSpecDecl) -> FormSchema | None:
        return None
