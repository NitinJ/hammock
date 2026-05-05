"""Unit tests for engine/v1/substrate.py."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from engine.v1.substrate import (
    SubstrateError,
    allocate_code_substrate,
    set_up_job_repo,
)
from shared.v1 import paths


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
        self._fs_side_effects: list[Callable[[list[str]], None]] = []

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

    def add_side_effect(self, fn: Callable[[list[str]], None]) -> None:
        self._fs_side_effects.append(fn)

    def __call__(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(_Call(list(args), cwd))
        for fn in self._fs_side_effects:
            fn(list(args))
        best: tuple[str, ...] | None = None
        for prefix in self._handlers:
            if tuple(args[: len(prefix)]) == prefix:
                if best is None or len(prefix) > len(best):
                    best = prefix
        if best is None:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")
        return self._handlers[best](args)


def _clone_creates_dest(args: list[str]) -> None:
    """Side effect: pretend `git clone <url> <dest>` made <dest>/.git."""
    if args[:2] == ["git", "clone"] and len(args) >= 4:
        dest = Path(args[3])
        (dest / ".git").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# set_up_job_repo
# ---------------------------------------------------------------------------


def test_set_up_creates_clone_branch_pushes(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.add_side_effect(_clone_creates_dest)
    runner.expect(("git", "show-ref"), returncode=1)  # job branch missing locally
    runner.expect(("git", "ls-remote"), returncode=2)  # job branch missing remote

    repo = set_up_job_repo(
        job_slug="j",
        root=tmp_path,
        repo_url="https://github.com/me/repo",
        repo_slug="me/repo",
        runner=runner,
    )
    assert repo.repo_dir == paths.repo_clone_dir("j", root=tmp_path)
    assert repo.job_branch == "hammock/jobs/j"
    assert repo.repo_slug == "me/repo"

    cmds = [c.args for c in runner.calls]
    assert any(c[:2] == ["git", "clone"] for c in cmds)
    assert any(
        c == ["git", "fetch", "origin", "main"] for c in cmds
    )
    assert any(
        c == ["git", "branch", "hammock/jobs/j", "origin/main"] for c in cmds
    )
    assert any(
        c == ["git", "push", "-u", "origin", "hammock/jobs/j"] for c in cmds
    )


def test_set_up_idempotent_when_job_branch_already_remote(tmp_path: Path) -> None:
    """Re-running submit (e.g. resume) shouldn't re-push or re-create."""
    runner = FakeRunner()
    runner.add_side_effect(_clone_creates_dest)
    runner.expect(("git", "show-ref"), returncode=0)  # already exists locally
    runner.expect(("git", "ls-remote"), returncode=0, stdout="<sha>\trefs/heads/x")

    set_up_job_repo(
        job_slug="j",
        root=tmp_path,
        repo_url="https://github.com/me/repo",
        repo_slug="me/repo",
        runner=runner,
    )
    cmds = [c.args for c in runner.calls]
    assert not any(c[:2] == ["git", "branch"] for c in cmds)
    assert not any(c[:2] == ["git", "push"] for c in cmds)


def test_set_up_raises_on_branch_create_failure(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.add_side_effect(_clone_creates_dest)
    runner.expect(("git", "show-ref"), returncode=1)
    runner.expect(("git", "branch"), returncode=128, stderr="bad")

    with pytest.raises(SubstrateError, match="job branch"):
        set_up_job_repo(
            job_slug="j",
            root=tmp_path,
            repo_url="https://github.com/me/repo",
            repo_slug="me/repo",
            runner=runner,
        )


def test_set_up_raises_on_push_failure(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.add_side_effect(_clone_creates_dest)
    runner.expect(("git", "show-ref"), returncode=1)
    runner.expect(("git", "ls-remote"), returncode=2)
    runner.expect(("git", "push"), returncode=1, stderr="rejected")

    with pytest.raises(SubstrateError, match="push job branch"):
        set_up_job_repo(
            job_slug="j",
            root=tmp_path,
            repo_url="https://github.com/me/repo",
            repo_slug="me/repo",
            runner=runner,
        )


# ---------------------------------------------------------------------------
# allocate_code_substrate
# ---------------------------------------------------------------------------


def test_allocate_creates_stage_branch_and_worktree(tmp_path: Path) -> None:
    """Sets up the engine repo first, then allocates a code substrate
    for one node."""
    runner = FakeRunner()
    runner.add_side_effect(_clone_creates_dest)
    # Initial set_up_job_repo: branch missing locally, missing remotely.
    runner.expect(("git", "show-ref"), returncode=1)
    runner.expect(("git", "ls-remote"), returncode=2)

    job_repo = set_up_job_repo(
        job_slug="j",
        root=tmp_path,
        repo_url="https://github.com/me/repo",
        repo_slug="me/repo",
        runner=runner,
    )

    # Re-arm: stage branch missing for the next show-ref call.
    runner.expect(("git", "show-ref"), returncode=1)

    sub = allocate_code_substrate(
        job_slug="j",
        node_id="implement",
        root=tmp_path,
        job_repo=job_repo,
        runner=runner,
    )
    assert sub.repo_dir == paths.repo_clone_dir("j", root=tmp_path)
    assert sub.worktree == paths.node_worktree_dir(
        "j", "implement", root=tmp_path
    )
    assert sub.stage_branch == "hammock/stages/j/implement"
    assert sub.base_branch == "hammock/jobs/j"
    assert sub.repo_slug == "me/repo"

    cmds = [c.args for c in runner.calls]
    # Pulls job branch BEFORE forking.
    fetch_calls = [
        i
        for i, c in enumerate(cmds)
        if c == ["git", "fetch", "origin", "hammock/jobs/j"]
    ]
    assert len(fetch_calls) == 1
    branch_create = next(
        (i for i, c in enumerate(cmds)
         if c == ["git", "branch", "hammock/stages/j/implement", "origin/hammock/jobs/j"]),
        None,
    )
    assert branch_create is not None
    assert branch_create > fetch_calls[0]

    # Worktree add invoked at the right path.
    assert any(
        c[:3] == ["git", "worktree", "add"]
        and c[3] == str(paths.node_worktree_dir("j", "implement", root=tmp_path))
        and c[4] == "hammock/stages/j/implement"
        for c in cmds
    )
