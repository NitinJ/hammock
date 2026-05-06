"""``pr-review-verdict`` variable type.

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

import json
import os
import subprocess
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from shared.v1.types.protocol import (
    FormSchema,
    NodeContext,
    PromptContext,
    VariableTypeError,
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


class _SubmissionShape(BaseModel):
    """Shape of the raw payload the human submits via the dashboard.
    Just the verdict — summary is engine-populated."""

    model_config = ConfigDict(extra="forbid")

    verdict: Literal["merged", "needs-revision"]


def _extract_pr_url(pr_input: object) -> str:
    """Pull the URL off whatever the resolver materialised for the
    upstream pr variable. Pydantic ``Value`` instance, dict, or
    structural object — accept any with a ``url`` field/key."""
    url = getattr(pr_input, "url", None)
    if url is None and isinstance(pr_input, dict):
        url = pr_input.get("url")
    if not isinstance(url, str) or not url:
        raise VariableTypeError(
            f"pr-review-verdict: upstream `pr` input has no `url` (got {type(pr_input).__name__})"
        )
    return url


def _gh_env() -> dict[str, str]:
    """gh sometimes injects ANSI color escapes in non-tty contexts;
    NO_COLOR=1 disables them so JSON parsing stays clean."""
    return {**os.environ, "NO_COLOR": "1"}


def _format_pr_feedback(data: dict[str, Any]) -> str:
    """Render reviewer comments, review-level feedback, and failing
    checks into one prose blob suitable for an agent prompt."""
    parts: list[str] = []

    for review in data.get("reviews") or []:
        author = (review.get("author") or {}).get("login", "?")
        state = review.get("state", "")
        body = (review.get("body") or "").strip()
        if body:
            parts.append(f"Review by {author} ({state}): {body}")

    for comment in data.get("comments") or []:
        author = (comment.get("author") or {}).get("login", "?")
        body = (comment.get("body") or "").strip()
        if body:
            parts.append(f"Comment by {author}: {body}")

    failing = [
        c
        for c in (data.get("statusCheckRollup") or [])
        if (c.get("conclusion") or "").upper() in {"FAILURE", "CANCELLED", "TIMED_OUT"}
    ]
    if failing:
        names = ", ".join((c.get("name") or "?") for c in failing)
        parts.append(f"Failing checks: {names}")

    return "\n\n".join(parts)


class PRReviewVerdictType:
    name: ClassVar[str] = "pr-review-verdict"
    Decl: ClassVar[type[PRReviewVerdictDecl]] = PRReviewVerdictDecl
    Value: ClassVar[type[PRReviewVerdictValue]] = PRReviewVerdictValue

    def produce(self, decl: PRReviewVerdictDecl, ctx: NodeContext) -> PRReviewVerdictValue:
        """Read submission, verify via gh, populate summary, return."""
        # 1. Read the human's submission.
        path = ctx.expected_path()
        if not path.is_file():
            raise VariableTypeError(f"pr-review-verdict not produced at {path}")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise VariableTypeError(
                f"pr-review-verdict at {path} is not valid JSON: {exc}"
            ) from exc

        try:
            submission = _SubmissionShape.model_validate(data)
        except ValidationError as exc:
            raise VariableTypeError(f"pr-review-verdict submission invalid: {exc}") from exc

        # 2. Resolve upstream pr URL from ctx.inputs.
        pr_input = ctx.inputs.get("pr")
        if pr_input is None:
            raise VariableTypeError("pr-review-verdict: no upstream `pr` input on this node")
        pr_url = _extract_pr_url(pr_input)

        # 3. Branch on verdict.
        if submission.verdict == "merged":
            return self._verify_merged(pr_url)
        return self._aggregate_needs_revision(pr_url)

    def _verify_merged(self, pr_url: str) -> PRReviewVerdictValue:
        result = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
            check=False,
            env=_gh_env(),
        )
        if result.returncode != 0:
            raise VariableTypeError(
                f"pr-review-verdict: could not verify PR at {pr_url} via gh: "
                f"{result.stderr.strip()}"
            )
        state = result.stdout.strip()
        if state != "MERGED":
            raise VariableTypeError(
                f"PR at {pr_url} is in state {state!r}, not MERGED. "
                "Merge it on GitHub first, then re-submit."
            )
        return PRReviewVerdictValue(verdict="merged", summary="")

    def _aggregate_needs_revision(self, pr_url: str) -> PRReviewVerdictValue:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                pr_url,
                "--json",
                "comments,reviews,statusCheckRollup",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=_gh_env(),
        )
        if result.returncode != 0:
            raise VariableTypeError(
                f"pr-review-verdict: could not fetch PR feedback at {pr_url} "
                f"via gh: {result.stderr.strip()}"
            )
        stdout = result.stdout.strip()
        if not stdout:
            data: dict[str, Any] = {}
        else:
            try:
                data = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise VariableTypeError(
                    f"pr-review-verdict: gh returned non-JSON for {pr_url}: {exc}"
                ) from exc
        summary = _format_pr_feedback(data)
        return PRReviewVerdictValue(verdict="needs-revision", summary=summary)

    def render_for_producer(self, decl: PRReviewVerdictDecl, ctx: PromptContext) -> str:
        # Not directly producer-rendered — humans see a form. This
        # string surfaces only if a flow renders it (e.g., for resume).
        return (
            f"### Output `{ctx.var_name}` (pr-review-verdict)\n\n"
            'Pick one of: `{"verdict": "merged"}` (you merged the PR on '
            'GitHub) or `{"verdict": "needs-revision"}` (you left feedback; '
            "Hammock will pull comments + reviews + check status from gh "
            "into the summary)."
        )

    def render_for_consumer(
        self,
        decl: PRReviewVerdictDecl,
        value: PRReviewVerdictValue,
        ctx: PromptContext,
    ) -> str:
        lines = [
            f"### Input `{ctx.var_name}` (pr-review-verdict)",
            "",
            f"**Verdict:** {value.verdict}",
        ]
        if value.summary:
            lines.append(f"**Summary:**\n\n{value.summary}")
        return "\n".join(lines)

    def form_schema(self, decl: PRReviewVerdictDecl) -> FormSchema | None:
        """Two buttons, no textarea — the human picks one verdict; the
        engine populates summary from gh."""
        return FormSchema(
            fields=[("verdict", "select:merged,needs-revision")],
        )
