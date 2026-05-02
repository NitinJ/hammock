"""Review verdict schema.

Per design doc § Plan Compiler § Review pattern and verdict schema. The
canonical shape for every review-style stage in the system. One review stage
produces exactly one verdict file matching this schema, regardless of how
many internal opinions it consulted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewConcern(BaseModel):
    """A single concern raised by a reviewer."""

    model_config = ConfigDict(extra="forbid")

    severity: Literal["blocker", "major", "minor"]
    concern: str = Field(min_length=1)
    location: str = Field(
        min_length=1,
        description="file path, section, line range, or 'general'",
    )


class ReviewVerdict(BaseModel):
    """The canonical verdict produced by any review stage."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["approved", "needs-revision", "rejected"]
    summary: str = Field(
        min_length=1,
        description="1-3 sentence synthesis of the review",
    )
    unresolved_concerns: list[ReviewConcern] = Field(default_factory=list)
    addressed_in_this_iteration: list[str] = Field(
        default_factory=list,
        description=(
            "empty on iter 1; populated on iter 2+ to acknowledge what the "
            "previous round of feedback got fixed"
        ),
    )
