"""`pr-merge-confirmation` variable type.

Per design-patch §3.4: a HIL gate where the human merges a PR on GitHub
(out-of-band) and clicks "merged" in Hammock. The engine verifies the
PR is actually merged before accepting the submission, via the type's
``produce`` running synchronously inside the submission API.

For T4 the type queries gh directly via subprocess. The check is:
``gh pr view <url> --json state`` — state must be "MERGED".
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from shared.v1.types.protocol import (
    FormSchema,
    NodeContext,
    PromptContext,
    VariableTypeError,
)


class PRMergeConfirmationDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # No per-variable config in v1.


class PRMergeConfirmationValue(BaseModel):
    """The value persisted after the human clicks 'merged' AND the engine
    verifies via gh that the PR is actually merged."""

    model_config = ConfigDict(extra="forbid")

    pr_url: str = Field(..., min_length=1)
    """The PR URL the human confirmed merging."""

    pr_number: int = Field(..., ge=1)
    """Parsed from the URL for convenience."""


_PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


class PRMergeConfirmationType:
    name: ClassVar[str] = "pr-merge-confirmation"
    Decl: ClassVar[type[PRMergeConfirmationDecl]] = PRMergeConfirmationDecl
    Value: ClassVar[type[PRMergeConfirmationValue]] = PRMergeConfirmationValue

    def produce(self, decl: PRMergeConfirmationDecl, ctx: NodeContext) -> PRMergeConfirmationValue:
        """Read the human's submission (just the pr_url) from disk, then
        query gh to confirm the PR is merged. Reject otherwise."""
        path = ctx.expected_path()
        if not path.is_file():
            raise VariableTypeError(f"pr-merge-confirmation not produced at {path}")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise VariableTypeError(
                f"pr-merge-confirmation at {path} is not valid JSON: {exc}"
            ) from exc

        pr_url = data.get("pr_url")
        if not pr_url or not isinstance(pr_url, str):
            raise VariableTypeError("submission must include a non-empty `pr_url` string")

        # Verify against GitHub. Use --jq to extract a raw scalar string
        # so we don't have to parse gh's JSON output (which sometimes
        # carries ANSI color escapes when run in a non-tty subprocess).
        # NO_COLOR also disables those escapes as belt-and-braces.
        import os as _os

        env = {**_os.environ, "NO_COLOR": "1"}
        result = subprocess.run(
            ["gh", "pr", "view", pr_url, "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            raise VariableTypeError(
                f"could not verify PR at {pr_url} via gh: {result.stderr.strip()}"
            )
        state = result.stdout.strip()
        if state != "MERGED":
            raise VariableTypeError(
                f"PR at {pr_url} is in state {state!r}, "
                "not MERGED. Merge it on GitHub first, then re-submit."
            )

        match = _PR_NUMBER_RE.search(pr_url)
        if not match:
            raise VariableTypeError(f"could not parse PR number from {pr_url!r}")
        return PRMergeConfirmationValue(pr_url=pr_url, pr_number=int(match.group(1)))

    def render_for_producer(self, decl: PRMergeConfirmationDecl, ctx: PromptContext) -> str:
        # Not directly producer-rendered (humans see a form). Form
        # schema is below; this string is shown if any flow renders it.
        return (
            f"### Output `{ctx.var_name}` (pr-merge-confirmation)\n\n"
            'Merge the PR on GitHub, then submit `{"pr_url": "<url>"}`. '
            "The engine will verify via `gh pr view` that the PR is in state "
            "`MERGED` before accepting."
        )

    def render_for_consumer(
        self,
        decl: PRMergeConfirmationDecl,
        value: PRMergeConfirmationValue,
        ctx: PromptContext,
    ) -> str:
        return (
            f"### Input `{ctx.var_name}` (pr-merge-confirmation)\n\n"
            f"PR #{value.pr_number} merged: {value.pr_url}"
        )

    def form_schema(self, decl: PRMergeConfirmationDecl) -> FormSchema | None:
        return FormSchema(fields=[("pr_url", "url")])
