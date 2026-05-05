"""Unit tests for shared/v1/types/pr.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from shared.v1.types.pr import PRDecl, PRType, PRValue
from shared.v1.types.protocol import VariableTypeError


@dataclass
class FakeCodeNodeCtx:
    """Stand-in for a `code`-kind NodeContext. Records calls for inspection."""

    var_name: str
    job_dir: Path
    stage_branch: str = "hammock/stages/job/n"
    base_branch: str = "hammock/jobs/job"
    repo: str = "me/repo"

    has_commits: bool = True
    pushes: list[str] = field(default_factory=list)
    pr_url: str = "https://github.com/me/repo/pull/42"
    commit_subject: str = "fix(add_integers): empty case returns 0"
    commit_body: str = "Body line one.\nBody line two."

    def expected_path(self) -> Path:
        return self.job_dir / "variables" / f"{self.var_name}.json"

    def branch_has_commits(self, branch: str, *, base: str) -> bool:
        return self.has_commits

    def git_push(self, branch: str) -> None:
        self.pushes.append(branch)

    def gh_create_pr(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool = False,
    ) -> str:
        self.last_create_kwargs: dict[str, Any] = {
            "head": head,
            "base": base,
            "title": title,
            "body": body,
            "draft": draft,
        }
        return self.pr_url

    def latest_commit_subject(self, branch: str) -> str:
        return self.commit_subject

    def latest_commit_body(self, branch: str) -> str:
        return self.commit_body


@dataclass
class FakeArtifactCtx:
    """Stand-in NodeContext with NO code-substrate fields — used to
    verify PR.produce raises when called for an artifact node."""

    var_name: str
    job_dir: Path

    def expected_path(self) -> Path:
        return self.job_dir / "variables" / f"{self.var_name}.json"


@dataclass
class FakePromptCtx:
    var_name: str
    job_dir: Path
    actor_workdir: str | None = None
    stage_branch: str | None = None
    base_branch: str | None = None

    def expected_path(self) -> Path:
        return self.job_dir / "variables" / f"{self.var_name}.json"


# ---------------------------------------------------------------------------
# Decl + Value
# ---------------------------------------------------------------------------


def test_pr_decl_default_not_draft() -> None:
    assert PRDecl().draft is False


def test_pr_value_validates() -> None:
    v = PRValue(
        url="https://github.com/me/repo/pull/1",
        number=1,
        branch="hammock/stages/job/n",
        base="hammock/jobs/job",
        repo="me/repo",
    )
    assert v.number == 1


# ---------------------------------------------------------------------------
# produce — happy path
# ---------------------------------------------------------------------------


def test_produce_pushes_then_opens_pr_returns_record(tmp_path: Path) -> None:
    t = PRType()
    ctx = FakeCodeNodeCtx(var_name="pr", job_dir=tmp_path)
    value = t.produce(t.Decl(), ctx)
    assert isinstance(value, PRValue)
    assert value.url == "https://github.com/me/repo/pull/42"
    assert value.number == 42
    assert value.branch == "hammock/stages/job/n"
    assert value.base == "hammock/jobs/job"
    assert value.repo == "me/repo"
    # Engine actually called the helpers in the right order.
    assert ctx.pushes == ["hammock/stages/job/n"]
    assert ctx.last_create_kwargs == {
        "head": "hammock/stages/job/n",
        "base": "hammock/jobs/job",
        "title": "fix(add_integers): empty case returns 0",
        "body": "Body line one.\nBody line two.",
        "draft": False,
    }


def test_produce_passes_draft_through(tmp_path: Path) -> None:
    t = PRType()
    ctx = FakeCodeNodeCtx(var_name="pr", job_dir=tmp_path)
    t.produce(PRDecl(draft=True), ctx)
    assert ctx.last_create_kwargs["draft"] is True


def test_produce_default_body_when_commit_body_empty(tmp_path: Path) -> None:
    t = PRType()
    ctx = FakeCodeNodeCtx(var_name="pr", job_dir=tmp_path, commit_body="")
    t.produce(t.Decl(), ctx)
    assert "Auto-opened by Hammock" in ctx.last_create_kwargs["body"]


# ---------------------------------------------------------------------------
# produce — failure paths
# ---------------------------------------------------------------------------


def test_produce_raises_when_branch_has_no_commits(tmp_path: Path) -> None:
    t = PRType()
    ctx = FakeCodeNodeCtx(var_name="pr", job_dir=tmp_path, has_commits=False)
    with pytest.raises(VariableTypeError, match="no commits beyond"):
        t.produce(t.Decl(), ctx)
    # No push attempt either.
    assert ctx.pushes == []


def test_produce_raises_when_ctx_is_not_code_kind(tmp_path: Path) -> None:
    """An `artifact`-kind ctx lacks the substrate fields. produce must
    raise rather than silently succeed."""
    t = PRType()
    ctx = FakeArtifactCtx(var_name="pr", job_dir=tmp_path)
    with pytest.raises(VariableTypeError, match="code` kind"):
        t.produce(t.Decl(), ctx)  # type: ignore[arg-type]


def test_produce_raises_on_unparseable_url(tmp_path: Path) -> None:
    t = PRType()
    ctx = FakeCodeNodeCtx(
        var_name="pr",
        job_dir=tmp_path,
        pr_url="https://example.com/notaprrurl",
    )
    with pytest.raises(VariableTypeError, match="parse PR number"):
        t.produce(t.Decl(), ctx)


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def test_render_for_producer_includes_worktree_and_branch_hints(
    tmp_path: Path,
) -> None:
    t = PRType()
    ctx = FakePromptCtx(
        var_name="pr",
        job_dir=tmp_path,
        actor_workdir="/tmp/wt",
        stage_branch="hammock/stages/job/n",
        base_branch="hammock/jobs/job",
    )
    rendered = t.render_for_producer(t.Decl(), ctx)
    assert "/tmp/wt" in rendered
    assert "hammock/stages/job/n" in rendered
    assert "hammock/jobs/job" in rendered
    assert "Do not run" in rendered  # the engine-not-agent reminder


def test_render_for_producer_draft_note_only_when_draft(tmp_path: Path) -> None:
    t = PRType()
    ctx = FakePromptCtx(
        var_name="pr",
        job_dir=tmp_path,
        actor_workdir="/wt",
        stage_branch="b",
        base_branch="bb",
    )
    plain = t.render_for_producer(PRDecl(draft=False), ctx)
    draft = t.render_for_producer(PRDecl(draft=True), ctx)
    assert "draft" not in plain.lower()
    assert "draft" in draft.lower()


def test_render_for_consumer_summarises_pr(tmp_path: Path) -> None:
    t = PRType()
    value = PRValue(
        url="https://github.com/me/repo/pull/7",
        number=7,
        branch="hammock/stages/j/x",
        base="hammock/jobs/j",
        repo="me/repo",
    )
    ctx = FakePromptCtx(var_name="pr", job_dir=tmp_path)
    rendered = t.render_for_consumer(t.Decl(), value, ctx)
    assert "PR #7" in rendered
    assert "https://github.com/me/repo/pull/7" in rendered
    assert "hammock/stages/j/x" in rendered


def test_form_schema_returns_none_for_pr() -> None:
    """`pr` is engine-produced; never human-producible."""
    t = PRType()
    assert t.form_schema(t.Decl()) is None


# ---------------------------------------------------------------------------
# Registry registration
# ---------------------------------------------------------------------------


def test_pr_registered_in_closed_set() -> None:
    from shared.v1.types.registry import REGISTRY, get_type

    assert "pr" in REGISTRY
    t = get_type("pr")
    assert t.name == "pr"
