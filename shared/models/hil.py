"""HIL bridge schemas.

Per design doc § HIL bridge § HIL typed shapes. Three kinds (``ask``,
``review``, ``manual-step``); each has a question and an answer schema. The
``HilItem`` envelope is a Pydantic discriminated union on ``kind``.

Persisted to ``jobs/<id>/hil/<item_id>.json``. The dashboard MCP server
creates items; the dashboard appends the answer field on submit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Question / answer pairs
# ---------------------------------------------------------------------------


class AskQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ask"] = "ask"
    text: str = Field(min_length=1)
    options: list[str] | None = None


class AskAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["ask"] = "ask"
    choice: str | None = None
    text: str = Field(min_length=1)


class ReviewQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["review"] = "review"
    target: str = Field(min_length=1, description="path of artifact under review")
    prompt: str = Field(min_length=1)


class ReviewAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["review"] = "review"
    decision: Literal["approve", "reject"]
    comments: str


class ManualStepQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["manual-step"] = "manual-step"
    instructions: str = Field(min_length=1)
    extra_fields: dict[str, Any] | None = None


class ManualStepAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["manual-step"] = "manual-step"
    output: str
    extras: dict[str, Any] | None = None


# Discriminated unions ------------------------------------------------------

HilQuestion = Annotated[
    AskQuestion | ReviewQuestion | ManualStepQuestion,
    Field(discriminator="kind"),
]

HilAnswer = Annotated[
    AskAnswer | ReviewAnswer | ManualStepAnswer,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


class HilItem(BaseModel):
    """Persisted HIL item with embedded question and (post-answer) answer."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["ask", "review", "manual-step"]
    stage_id: str = Field(min_length=1)
    task_id: str | None = None
    created_at: datetime
    status: Literal["awaiting", "answered", "cancelled"]

    question: HilQuestion
    answer: HilAnswer | None = None
    answered_at: datetime | None = None

    @model_validator(mode="after")
    def _kinds_must_align(self) -> Self:
        """Outer ``kind`` and the question's (and answer's) ``kind`` must match."""
        if self.question.kind != self.kind:
            raise ValueError(f"HilItem.kind={self.kind!r} but question.kind={self.question.kind!r}")
        if self.answer is not None and self.answer.kind != self.kind:
            raise ValueError(f"HilItem.kind={self.kind!r} but answer.kind={self.answer.kind!r}")
        return self
