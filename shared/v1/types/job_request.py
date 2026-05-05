"""`job-request` variable type — the user's initial input to a workflow.

A simple typed text container. The user provides this at job submission
(via CLI / dashboard); the engine writes it as the first variable on
disk before any node runs. Downstream nodes read it as input.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from shared.v1.types.protocol import (
    FormSchema,
    NodeContext,
    PromptContext,
    VariableTypeError,
)


class JobRequestDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # No per-variable config in v1.


class JobRequestValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    """The user's request text — what they want done."""


class JobRequestType:
    name: ClassVar[str] = "job-request"
    Decl: ClassVar[type[JobRequestDecl]] = JobRequestDecl
    Value: ClassVar[type[JobRequestValue]] = JobRequestValue

    def produce(self, decl: JobRequestDecl, ctx: NodeContext) -> JobRequestValue:
        """The job-request variable is written by the engine at job-submit
        time, not produced by an agent. If `produce` is called for it
        post-actor, that's a wiring bug — the variable should already exist."""
        raise VariableTypeError(
            "job-request is engine-produced at job submission; no agent "
            "node should declare it as an output"
        )

    def render_for_producer(self, decl: JobRequestDecl, ctx: PromptContext) -> str:
        # Not user-producible; included for protocol completeness.
        return ""

    def render_for_consumer(
        self, decl: JobRequestDecl, value: JobRequestValue, ctx: PromptContext
    ) -> str:
        return f"### Input `{ctx.var_name}` (job-request)\n\n{value.text}"

    def form_schema(self, decl: JobRequestDecl) -> FormSchema | None:
        # Not human-producible; engine writes it.
        return None
