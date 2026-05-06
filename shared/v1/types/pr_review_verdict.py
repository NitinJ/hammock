"""``pr-review-verdict`` variable type — Stage 2 step-0 stub.

Per design-patch §9.4 / impl-patch §Stage 2:

A HIL gate where the human acts on a PR on GitHub (merge it, or leave
review comments) and clicks one of two buttons in Hammock:

- ``Merged`` — they merged the PR; engine verifies via ``gh pr view``.
- ``Pending review`` — they left feedback; engine fetches comments,
  reviews, and check status from gh and aggregates into ``summary`` so
  the next implement iteration sees one prose blob.

The human submission payload is minimal: ``{verdict: "merged" |
"needs-revision"}``. The ``summary`` field is engine-populated, never
human-typed. GitHub becomes the single surface for review activity.

Replaces the v0 ``pr-merge-confirmation`` type.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from shared.v1.types.protocol import (
    FormSchema,
    NodeContext,
    PromptContext,
)


class PRReviewVerdictDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # No per-variable config in v1.


class PRReviewVerdictValue(BaseModel):
    """Engine-populated value for a PR review HIL gate.

    The human submits only ``verdict``; ``summary`` is set by the
    engine's ``produce`` from gh data."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["merged", "needs-revision"]
    """``merged``  — human merged the PR on GitHub.
    ``needs-revision`` — human left feedback / checks failed."""

    summary: str = ""
    """Engine-populated. On ``merged``: empty or short confirmation.
    On ``needs-revision``: aggregated prose covering reviewer comments,
    inline review comments, and failing CI checks fetched via
    ``gh pr view --json comments,reviews,statusCheckRollup``."""


class PRReviewVerdictType:
    name: ClassVar[str] = "pr-review-verdict"
    Decl: ClassVar[type[PRReviewVerdictDecl]] = PRReviewVerdictDecl
    Value: ClassVar[type[PRReviewVerdictValue]] = PRReviewVerdictValue

    def produce(
        self, decl: PRReviewVerdictDecl, ctx: NodeContext
    ) -> PRReviewVerdictValue:
        """Read the human's submission (only ``verdict``) from disk; read
        the upstream ``pr`` input from ``ctx.inputs["pr"]`` to get the
        URL; query ``gh`` to verify (on merged) or fetch feedback (on
        needs-revision); return the populated value.

        Step 0 stub — Step 2 implements."""
        raise NotImplementedError

    def render_for_producer(
        self, decl: PRReviewVerdictDecl, ctx: PromptContext
    ) -> str:
        """Step 0 stub — Step 2 implements."""
        raise NotImplementedError

    def render_for_consumer(
        self,
        decl: PRReviewVerdictDecl,
        value: PRReviewVerdictValue,
        ctx: PromptContext,
    ) -> str:
        """Step 0 stub — Step 2 implements."""
        raise NotImplementedError

    def form_schema(
        self, decl: PRReviewVerdictDecl
    ) -> FormSchema | None:
        """Two buttons, no textarea — the human picks one verdict; the
        engine populates summary from gh."""
        return FormSchema(
            fields=[("verdict", "select:merged,needs-revision")],
        )
