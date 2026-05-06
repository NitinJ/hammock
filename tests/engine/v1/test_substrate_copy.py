"""Unit tests for engine/v1/substrate.copy_local_repo.

Per ``docs/projects-management.md``: the engine copies the operator's
registered local checkout into ``<job_dir>/repo`` per job. Replaces
the older clone-from-URL flow.

Tests run against real filesystem (cp_R is a fs op, not a subprocess
we'd mock) but stub git ops via the same ``FakeRunner`` that
``test_substrate.py`` uses. We assert:

- Tracked + untracked + ``.env`` + ``.git`` all land in the copy.
- The job branch is created off ``default_branch``, not whatever HEAD
  pointed at in the source.
- Push to origin happens once per job; idempotent on re-run.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from engine.v1.substrate import (
    SubstrateError,
    copy_local_repo,
)
from shared.v1 import paths

# ---------------------------------------------------------------------------
# FakeRunner — same shape as test_substrate.py's helper
# ---------------------------------------------------------------------------


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
# Source repo fixture
# ---------------------------------------------------------------------------


def _make_source_repo(parent: Path) -> Path:
    """Lay down a minimal source tree with the kinds of files Hammock
    is asked to preserve: tracked, untracked, dotfile (``.env``), and
    a fake ``.git/`` directory."""
    src = parent / "src-repo"
    src.mkdir()
    (src / "README.md").write_text("# tracked file\n")
    (src / "src").mkdir()
    (src / "src" / "main.py").write_text("# tracked\n")
    (src / ".env").write_text("DATABASE_URL=postgres://localhost/test\n")
    (src / "node_modules").mkdir()
    (src / "node_modules" / "ignored.txt").write_text("(would be ignored later)\n")
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://github.com/me/repo.git\n'
    )
    return src


# ---------------------------------------------------------------------------
# copy_local_repo — happy path
# ---------------------------------------------------------------------------


def test_copy_creates_repo_dir_with_tracked_and_untracked(tmp_path: Path) -> None:
    """Full ``cp -R`` semantics: tracked, untracked, .env, .git all land."""
    src = _make_source_repo(tmp_path)
    runner = FakeRunner()
    runner.expect(("git", "show-ref"), returncode=1)  # job branch missing local
    runner.expect(("git", "ls-remote"), returncode=2)  # job branch missing remote

    repo = copy_local_repo(
        job_slug="j",
        root=tmp_path,
        repo_path=src,
        repo_slug="me/repo",
        default_branch="main",
        runner=runner,
    )

    dest = paths.repo_clone_dir("j", root=tmp_path)
    assert repo.repo_dir == dest
    assert repo.repo_slug == "me/repo"
    assert repo.job_branch == "hammock/jobs/j"
    assert (dest / "README.md").is_file()
    assert (dest / "src" / "main.py").is_file()
    # The whole point: untracked files like .env survive.
    assert (dest / ".env").read_text() == "DATABASE_URL=postgres://localhost/test\n"
    # node_modules also copied (no exclude list in v1; flagged as future
    # work in docs/projects-management.md).
    assert (dest / "node_modules" / "ignored.txt").is_file()
    # .git preserved so subsequent git ops + push work.
    assert (dest / ".git" / "config").is_file()


def test_copy_does_not_mutate_source(tmp_path: Path) -> None:
    """Operator's working tree is read-only to Hammock."""
    src = _make_source_repo(tmp_path)
    src_env = (src / ".env").stat().st_mtime
    runner = FakeRunner()
    runner.expect(("git", "show-ref"), returncode=1)
    runner.expect(("git", "ls-remote"), returncode=2)

    copy_local_repo(
        job_slug="j",
        root=tmp_path,
        repo_path=src,
        repo_slug="me/repo",
        default_branch="main",
        runner=runner,
    )

    # Source still intact, unmodified.
    assert (src / ".env").is_file()
    assert (src / ".env").stat().st_mtime == src_env
    assert (src / "README.md").is_file()


def test_copy_checks_out_default_branch_then_creates_job_branch(tmp_path: Path) -> None:
    """The operator's HEAD doesn't travel — we explicitly check out
    ``default_branch`` before branching ``hammock/jobs/<slug>``."""
    src = _make_source_repo(tmp_path)
    runner = FakeRunner()
    runner.expect(("git", "show-ref"), returncode=1)
    runner.expect(("git", "ls-remote"), returncode=2)

    copy_local_repo(
        job_slug="j",
        root=tmp_path,
        repo_path=src,
        repo_slug="me/repo",
        default_branch="develop",  # non-default name to prove we honour it
        runner=runner,
    )

    cmds = [c.args for c in runner.calls]
    # We must check out the default branch BEFORE creating the job branch,
    # otherwise `hammock/jobs/<slug>` would inherit the operator's HEAD.
    checkout_idx = next(i for i, c in enumerate(cmds) if c[:2] == ["git", "checkout"])
    branch_idx = next(
        i for i, c in enumerate(cmds) if c[:2] == ["git", "branch"] and "hammock/jobs/j" in c
    )
    assert checkout_idx < branch_idx, f"expected checkout before branch, got: {cmds}"
    # And the checkout target is the default branch.
    assert cmds[checkout_idx] == ["git", "checkout", "develop"]
    # And the branch is forked off the default branch (not from the
    # operator's HEAD).
    assert cmds[branch_idx] == ["git", "branch", "hammock/jobs/j", "develop"]


def test_copy_pushes_job_branch_to_origin(tmp_path: Path) -> None:
    src = _make_source_repo(tmp_path)
    runner = FakeRunner()
    runner.expect(("git", "show-ref"), returncode=1)
    runner.expect(("git", "ls-remote"), returncode=2)

    copy_local_repo(
        job_slug="j",
        root=tmp_path,
        repo_path=src,
        repo_slug="me/repo",
        default_branch="main",
        runner=runner,
    )

    cmds = [c.args for c in runner.calls]
    assert any(c == ["git", "push", "-u", "origin", "hammock/jobs/j"] for c in cmds), (
        f"expected job-branch push, got: {cmds}"
    )


def test_copy_idempotent_on_second_call(tmp_path: Path) -> None:
    """Re-submit / driver restart safety. A second call with the same
    ``job_slug`` doesn't re-copy or re-push."""
    src = _make_source_repo(tmp_path)
    runner = FakeRunner()
    runner.expect(("git", "show-ref"), returncode=1)
    runner.expect(("git", "ls-remote"), returncode=2)

    copy_local_repo(
        job_slug="j",
        root=tmp_path,
        repo_path=src,
        repo_slug="me/repo",
        default_branch="main",
        runner=runner,
    )

    # On second call: branch is local AND remote → no re-push.
    runner2 = FakeRunner()
    runner2.expect(("git", "show-ref"), returncode=0)  # branch present
    runner2.expect(("git", "ls-remote"), returncode=0, stdout="abcd refs/heads/hammock/jobs/j\n")

    copy_local_repo(
        job_slug="j",
        root=tmp_path,
        repo_path=src,
        repo_slug="me/repo",
        default_branch="main",
        runner=runner2,
    )
    cmds2 = [c.args for c in runner2.calls]
    assert not any(c[:2] == ["git", "push"] for c in cmds2), (
        "second call must not re-push the job branch"
    )


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------


def test_copy_raises_when_source_does_not_exist(tmp_path: Path) -> None:
    runner = FakeRunner()
    with pytest.raises(SubstrateError, match="repo_path"):
        copy_local_repo(
            job_slug="j",
            root=tmp_path,
            repo_path=tmp_path / "does-not-exist",
            repo_slug="me/repo",
            default_branch="main",
            runner=runner,
        )


def test_copy_raises_when_source_is_not_a_git_repo(tmp_path: Path) -> None:
    """Defensive check — even though the dashboard verifies before
    register, ``copy_local_repo`` re-asserts so a stale project.json
    that's been gitignored away gives a clear error."""
    src = tmp_path / "no-git"
    src.mkdir()
    (src / "README.md").write_text("not a repo\n")
    runner = FakeRunner()

    with pytest.raises(SubstrateError, match=r"not a git repo|\.git"):
        copy_local_repo(
            job_slug="j",
            root=tmp_path,
            repo_path=src,
            repo_slug="me/repo",
            default_branch="main",
            runner=runner,
        )
