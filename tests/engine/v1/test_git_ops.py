"""Unit tests for engine/v1/git_ops.py.

The tests use a FakeRunner so we don't need a real git/gh binary or a
real repo. The wrappers' job is to (1) construct the right argv,
(2) raise typed exceptions on non-zero rc, (3) parse stdout where
relevant. All three are testable without subprocess.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from engine.v1.git_ops import (
    GhError,
    GitError,
    add_worktree,
    branch_exists_local,
    branch_exists_remote,
    clone_repo,
    create_branch,
    fetch,
    gh_create_pr,
    has_commits_beyond,
    latest_commit_body,
    latest_commit_subject,
    push_branch,
)


@dataclasses.dataclass
class _Call:
    args: list[str]
    cwd: Path | None


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[_Call] = []
        self._handlers: dict[
            tuple[str, ...],
            Callable[[list[str]], subprocess.CompletedProcess[str]],
        ] = {}

    def expect(
        self,
        prefix: tuple[str, ...],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        def handler(args: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=args, returncode=returncode, stdout=stdout, stderr=stderr
            )

        self._handlers[prefix] = handler

    def __call__(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(_Call(list(args), cwd))
        best: tuple[str, ...] | None = None
        for prefix in self._handlers:
            if tuple(args[: len(prefix)]) == prefix:
                if best is None or len(prefix) > len(best):
                    best = prefix
        if best is None:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")
        return self._handlers[best](args)


# ---------------------------------------------------------------------------
# clone_repo
# ---------------------------------------------------------------------------


def test_clone_runs_git_clone(tmp_path: Path) -> None:
    runner = FakeRunner()

    # Make `git clone` create the destination so the idempotency check
    # matches what real clone does.
    def clone_handler(args: list[str]) -> subprocess.CompletedProcess[str]:
        Path(args[3]).mkdir(parents=True, exist_ok=True)
        (Path(args[3]) / ".git").mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    runner._handlers[("git", "clone")] = clone_handler
    dest = tmp_path / "repo"
    clone_repo("https://github.com/me/repo", dest, runner=runner)
    assert (dest / ".git").is_dir()
    assert any(c.args[:2] == ["git", "clone"] for c in runner.calls)


def test_clone_idempotent_when_dest_already_repo(tmp_path: Path) -> None:
    runner = FakeRunner()
    dest = tmp_path / "repo"
    (dest / ".git").mkdir(parents=True)
    clone_repo("https://github.com/me/repo", dest, runner=runner)
    # No git clone call should have been made.
    assert not any(c.args[:2] == ["git", "clone"] for c in runner.calls)


def test_clone_failure_raises(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "clone"), returncode=128, stderr="auth failed")
    with pytest.raises(GitError, match="git clone"):
        clone_repo("https://github.com/me/repo", tmp_path / "repo", runner=runner)


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


def test_fetch_runs_git_fetch(tmp_path: Path) -> None:
    runner = FakeRunner()
    fetch(tmp_path, ref="main", runner=runner)
    assert runner.calls[0].args == ["git", "fetch", "origin", "main"]
    assert runner.calls[0].cwd == tmp_path


def test_fetch_failure_raises(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "fetch"), returncode=1, stderr="no remote")
    with pytest.raises(GitError, match="git fetch"):
        fetch(tmp_path, runner=runner)


# ---------------------------------------------------------------------------
# branch_exists_*
# ---------------------------------------------------------------------------


def test_branch_exists_local_true(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "show-ref"), returncode=0)
    assert branch_exists_local(tmp_path, "x", runner=runner) is True


def test_branch_exists_local_false(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "show-ref"), returncode=1)
    assert branch_exists_local(tmp_path, "x", runner=runner) is False


def test_branch_exists_remote_true(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "ls-remote"), returncode=0, stdout="<sha>\trefs/heads/x")
    assert branch_exists_remote(tmp_path, "x", runner=runner) is True


def test_branch_exists_remote_false(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "ls-remote"), returncode=2)  # exit-code on no match
    assert branch_exists_remote(tmp_path, "x", runner=runner) is False


# ---------------------------------------------------------------------------
# create_branch
# ---------------------------------------------------------------------------


def test_create_branch_creates_when_missing(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "show-ref"), returncode=1)  # branch missing
    runner.expect(("git", "branch"), returncode=0)
    create_branch(tmp_path, "feature", "main", runner=runner)
    branch_calls = [c for c in runner.calls if c.args[:2] == ["git", "branch"]]
    assert len(branch_calls) == 1
    assert branch_calls[0].args == ["git", "branch", "feature", "main"]


def test_create_branch_idempotent_when_already_exists(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "show-ref"), returncode=0)  # already exists
    create_branch(tmp_path, "feature", "main", runner=runner)
    branch_calls = [c for c in runner.calls if c.args[:2] == ["git", "branch"]]
    assert branch_calls == []


def test_create_branch_failure_raises(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "show-ref"), returncode=1)
    runner.expect(("git", "branch"), returncode=128, stderr="bad")
    with pytest.raises(GitError, match="git branch"):
        create_branch(tmp_path, "feature", "main", runner=runner)


# ---------------------------------------------------------------------------
# push_branch
# ---------------------------------------------------------------------------


def test_push_branch_with_upstream(tmp_path: Path) -> None:
    runner = FakeRunner()
    push_branch(tmp_path, "feature", runner=runner)
    assert runner.calls[0].args == ["git", "push", "-u", "origin", "feature"]


def test_push_branch_no_upstream(tmp_path: Path) -> None:
    runner = FakeRunner()
    push_branch(tmp_path, "feature", upstream=False, runner=runner)
    assert runner.calls[0].args == ["git", "push", "origin", "feature"]


def test_push_branch_failure_raises(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "push"), returncode=1, stderr="rejected")
    with pytest.raises(GitError, match="git push"):
        push_branch(tmp_path, "x", runner=runner)


# ---------------------------------------------------------------------------
# add_worktree
# ---------------------------------------------------------------------------


def test_add_worktree_creates(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = tmp_path / "wt" / "n"
    add_worktree(repo, wt, "feature", runner=runner)
    assert any(c.args[:3] == ["git", "worktree", "add"] for c in runner.calls)


def test_add_worktree_idempotent_when_already_exists(tmp_path: Path) -> None:
    runner = FakeRunner()
    repo = tmp_path / "repo"
    repo.mkdir()
    wt = tmp_path / "wt" / "n"
    wt.mkdir(parents=True)
    runner.expect(
        ("git", "worktree", "list"),
        stdout=f"worktree {wt}\nHEAD <sha>\nbranch refs/heads/feature\n",
    )
    add_worktree(repo, wt, "feature", runner=runner)
    add_calls = [c for c in runner.calls if c.args[:3] == ["git", "worktree", "add"]]
    assert add_calls == []


# ---------------------------------------------------------------------------
# has_commits_beyond
# ---------------------------------------------------------------------------


def test_has_commits_beyond_true(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "rev-list"), stdout="3\n")
    assert has_commits_beyond(tmp_path, "feature", base="main", runner=runner) is True


def test_has_commits_beyond_false(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "rev-list"), stdout="0\n")
    assert has_commits_beyond(tmp_path, "feature", base="main", runner=runner) is False


def test_has_commits_beyond_failure_raises(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "rev-list"), returncode=128, stderr="bad")
    with pytest.raises(GitError, match="rev-list"):
        has_commits_beyond(tmp_path, "x", base="main", runner=runner)


# ---------------------------------------------------------------------------
# commit subject / body
# ---------------------------------------------------------------------------


def test_latest_commit_subject(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "log"), stdout="fix the bug\n")
    assert latest_commit_subject(tmp_path, "x", runner=runner) == "fix the bug"


def test_latest_commit_body(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("git", "log"), stdout="line 1\nline 2\n\n")
    assert latest_commit_body(tmp_path, "x", runner=runner) == "line 1\nline 2"


# ---------------------------------------------------------------------------
# gh_create_pr
# ---------------------------------------------------------------------------


def test_gh_create_pr_returns_url_from_stdout(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(
        ("gh", "pr", "create"),
        stdout="https://github.com/me/repo/pull/42\nDone.",
    )
    url = gh_create_pr(
        tmp_path,
        head="x",
        base="main",
        title="t",
        body="b",
        runner=runner,
    )
    assert url == "https://github.com/me/repo/pull/42"


def test_gh_create_pr_passes_draft_flag(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(
        ("gh", "pr", "create"),
        stdout="https://github.com/me/repo/pull/1",
    )
    gh_create_pr(
        tmp_path,
        head="x",
        base="main",
        title="t",
        body="b",
        draft=True,
        runner=runner,
    )
    assert "--draft" in runner.calls[0].args


def test_gh_create_pr_failure_raises(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("gh", "pr", "create"), returncode=1, stderr="conflict")
    with pytest.raises(GhError, match="gh pr create"):
        gh_create_pr(
            tmp_path,
            head="x",
            base="main",
            title="t",
            body="b",
            runner=runner,
        )


def test_gh_create_pr_no_url_in_stdout_raises(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("gh", "pr", "create"), stdout="(silence)")
    with pytest.raises(GhError, match="no PR URL"):
        gh_create_pr(
            tmp_path,
            head="x",
            base="main",
            title="t",
            body="b",
            runner=runner,
        )
