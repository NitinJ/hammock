"""`pr` variable type — a real GitHub pull request opened by the engine
on behalf of a code-kind agent node.

Per design-patch §1.4 + §6.3: the agent's only contract is to make code
edits in its worktree and commit. The engine handles `git push` and
`gh pr create` itself via this type's `produce`. The five-step prose
PR protocol that v0 injected into prompts collapses into one type
class.
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from shared.v1.types.protocol import (
    FormSchema,
    NodeContext,
    PromptContext,
    VariableTypeError,
)

_PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


class PRDecl(BaseModel):
    """Per-variable PR config. v1 minimum — extend as concrete needs land."""

    model_config = ConfigDict(extra="forbid")

    draft: bool = False
    """If true, open the PR as a draft."""


class PRValue(BaseModel):
    """The typed PR record persisted in the variable's envelope."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(..., min_length=1)
    """Full HTTPS URL of the PR (e.g. https://github.com/owner/repo/pull/42)."""

    number: int = Field(..., ge=1)
    """PR number on GitHub."""

    branch: str = Field(..., min_length=1)
    """The head branch the PR was opened from (typically the stage branch)."""

    base: str = Field(..., min_length=1)
    """The base branch the PR targets (typically the job branch)."""

    repo: str = Field(..., min_length=1)
    """``owner/repo`` slug."""


class PRType:
    name: ClassVar[str] = "pr"
    Decl: ClassVar[type[PRDecl]] = PRDecl
    Value: ClassVar[type[PRValue]] = PRValue

    def produce(self, decl: PRDecl, ctx: NodeContext) -> PRValue:
        """After the agent finishes editing + committing, the engine pushes
        the stage branch and opens a real PR.

        Requires `ctx` to expose code-substrate fields (stage_branch,
        base_branch, repo) and engine helpers (`branch_has_commits`,
        `git_push`, `gh_create_pr`, `latest_commit_subject`,
        `latest_commit_body`). Raises if any prereq is missing or the
        gh subprocess fails.
        """
        for attr in (
            "stage_branch",
            "base_branch",
            "repo",
            "branch_has_commits",
            "git_push",
            "gh_create_pr",
            "latest_commit_subject",
        ):
            if not hasattr(ctx, attr):
                raise VariableTypeError(
                    f"`pr` produce requires NodeContext with {attr!r}; the node must be `code` kind"
                )

        stage_branch = ctx.stage_branch  # type: ignore[attr-defined]
        base_branch = ctx.base_branch  # type: ignore[attr-defined]
        repo = ctx.repo  # type: ignore[attr-defined]

        if not ctx.branch_has_commits(stage_branch, base=base_branch):  # type: ignore[attr-defined]
            raise VariableTypeError(
                f"branch {stage_branch!r} has no commits beyond {base_branch!r} — "
                "agent did not commit any code changes"
            )

        ctx.git_push(stage_branch)  # type: ignore[attr-defined]

        title = ctx.latest_commit_subject(stage_branch)  # type: ignore[attr-defined]
        body = ""
        if hasattr(ctx, "latest_commit_body"):
            body = ctx.latest_commit_body(stage_branch)  # type: ignore[attr-defined]
        if not body.strip():
            body = f"Auto-opened by Hammock for variable `{ctx.var_name}`."

        url = ctx.gh_create_pr(  # type: ignore[attr-defined]
            head=stage_branch,
            base=base_branch,
            title=title,
            body=body,
            draft=decl.draft,
        )
        match = _PR_NUMBER_RE.search(url)
        if not match:
            raise VariableTypeError(
                f"could not parse PR number from URL {url!r} (expected pattern '/pull/<n>')"
            )
        return PRValue(
            url=url.strip(),
            number=int(match.group(1)),
            branch=stage_branch,
            base=base_branch,
            repo=repo,
        )

    def render_for_producer(self, decl: PRDecl, ctx: PromptContext) -> str:
        """Tell the agent: edit in the worktree, commit on the current
        branch. Engine handles push and PR creation."""
        # PromptContext carries minimum info; for code-kind we expose
        # the worktree + branch via additional attributes the dispatcher's
        # PromptCtx populates.
        worktree = getattr(ctx, "actor_workdir", None) or "<the working directory>"
        stage_branch = getattr(ctx, "stage_branch", None) or "<the current branch>"
        base_branch = getattr(ctx, "base_branch", None) or "the job branch"
        return (
            f"### Output `{ctx.var_name}` (pr)\n\n"
            f"Make code edits in the working directory: `{worktree}`.\n\n"
            f"Stage your changes and commit them on the current branch "
            f"(`{stage_branch}`). Use a meaningful commit message — its "
            "first line will become the PR title.\n\n"
            f"**Do not run `git push` or `gh pr create` yourself.** The "
            f"engine pushes the branch and opens a PR against `{base_branch}` "
            "after your stage exits.\n"
            + ("\n_The PR will be opened as a draft._\n" if decl.draft else "")
        )

    def render_for_consumer(self, decl: PRDecl, value: PRValue, ctx: PromptContext) -> str:
        return (
            f"### Input `{ctx.var_name}` (pr)\n\n"
            f"PR #{value.number}: {value.url}\n"
            f"Branch: `{value.branch}` → `{value.base}`\n"
            f"Repo: `{value.repo}`"
        )

    def form_schema(self, decl: PRDecl) -> FormSchema | None:
        # Not human-producible.
        return None
