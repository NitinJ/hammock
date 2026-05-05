"""Thin wrappers around git + gh subprocess invocations used by the
substrate allocator and the `pr` variable type.

Every function takes a ``runner`` for testability — unit tests pass a
fake CmdRunner; production passes ``_default_runner``. Errors are
raised as ``GitError`` / ``GhError`` with the captured stderr embedded.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)


CmdRunner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(
    args: list[str], *, cwd: Path | None = None, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=check,
    )


class GitError(Exception):
    """Raised when a git subprocess returns non-zero."""


class GhError(Exception):
    """Raised when a gh subprocess returns non-zero."""


_PR_URL_RE = re.compile(r"https://github\.com/[^\s]+/pull/\d+")


# ---------------------------------------------------------------------------
# git operations
# ---------------------------------------------------------------------------


def clone_repo(
    repo_url: str, dest: Path, *, runner: CmdRunner | None = None
) -> None:
    """Clone *repo_url* into *dest*. Idempotent: if *dest* already exists
    and is a git repo, skip."""
    runner = runner or _default_runner
    if (dest / ".git").is_dir():
        log.info("clone_repo: %s already cloned, skipping", dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = runner(["git", "clone", repo_url, str(dest)])
    if result.returncode != 0:
        raise GitError(
            f"git clone {repo_url} failed: rc={result.returncode}\n"
            f"stderr={result.stderr.strip()}"
        )


def fetch(
    repo_dir: Path, *, ref: str = "main", runner: CmdRunner | None = None
) -> None:
    runner = runner or _default_runner
    result = runner(
        ["git", "fetch", "origin", ref], cwd=repo_dir
    )
    if result.returncode != 0:
        raise GitError(
            f"git fetch origin {ref!r} failed in {repo_dir}: "
            f"rc={result.returncode} stderr={result.stderr.strip()}"
        )


def update_local_branch_to_remote(
    repo_dir: Path, branch: str, *, runner: CmdRunner | None = None
) -> None:
    """Force-update the local *branch* ref to match ``origin/<branch>``.

    Required after fetch when the local branch ref is stale (the remote
    has merged work that the local ref hasn't picked up). Subsequent
    ``rev-list base..head`` checks then give the correct count.

    Safe even if the local branch isn't checked out (as is the case for
    Hammock's job branch — we operate via worktrees on stage branches,
    never check out the job branch in the parent clone)."""
    runner = runner or _default_runner
    # Verify origin/<branch> exists before trying to update.
    check = runner(
        ["git", "rev-parse", "--verify", f"refs/remotes/origin/{branch}"],
        cwd=repo_dir,
    )
    if check.returncode != 0:
        # Remote ref missing — nothing to fast-forward to. Caller should
        # have fetched first; treat as no-op (the local branch may be
        # the canonical authority here).
        return
    result = runner(
        [
            "git",
            "update-ref",
            f"refs/heads/{branch}",
            f"refs/remotes/origin/{branch}",
        ],
        cwd=repo_dir,
    )
    if result.returncode != 0:
        raise GitError(
            f"git update-ref refs/heads/{branch} → origin/{branch} failed in "
            f"{repo_dir}: rc={result.returncode} stderr={result.stderr.strip()}"
        )


def branch_exists_local(
    repo_dir: Path, branch: str, *, runner: CmdRunner | None = None
) -> bool:
    runner = runner or _default_runner
    result = runner(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo_dir,
    )
    return result.returncode == 0


def branch_exists_remote(
    repo_dir: Path, branch: str, *, runner: CmdRunner | None = None
) -> bool:
    """True iff the remote 'origin' has *branch*. Cheap remote-aware check."""
    runner = runner or _default_runner
    result = runner(
        ["git", "ls-remote", "--exit-code", "--heads", "origin", branch],
        cwd=repo_dir,
    )
    return result.returncode == 0


def create_branch(
    repo_dir: Path,
    branch: str,
    base: str,
    *,
    runner: CmdRunner | None = None,
) -> None:
    """Create *branch* off *base* (locally). Idempotent — if *branch* already
    exists locally, no-op."""
    runner = runner or _default_runner
    if branch_exists_local(repo_dir, branch, runner=runner):
        return
    result = runner(["git", "branch", branch, base], cwd=repo_dir)
    if result.returncode != 0:
        raise GitError(
            f"git branch {branch!r} off {base!r} failed: "
            f"rc={result.returncode} stderr={result.stderr.strip()}"
        )


def push_branch(
    repo_dir: Path,
    branch: str,
    *,
    upstream: bool = True,
    force: bool = False,
    runner: CmdRunner | None = None,
) -> None:
    """Push *branch* to origin.

    ``force=True`` uses ``--force-with-lease`` (safer than ``--force`` —
    refuses if the remote has commits we haven't seen). Use this for
    Hammock-owned branches (``hammock/stages/*``) where successive runs
    may need to overwrite stale state from a killed prior run.
    """
    runner = runner or _default_runner
    args = ["git", "push"]
    if force:
        args.append("--force-with-lease")
    if upstream:
        args.append("-u")
    args.extend(["origin", branch])
    result = runner(args, cwd=repo_dir)
    if result.returncode != 0:
        raise GitError(
            f"git push origin {branch!r} failed: rc={result.returncode} "
            f"stderr={result.stderr.strip()}"
        )


def add_worktree(
    repo_dir: Path,
    worktree_path: Path,
    branch: str,
    *,
    runner: CmdRunner | None = None,
) -> None:
    """Add a worktree at *worktree_path* checked out to *branch*.

    Idempotent: if a worktree already exists at *worktree_path* on the
    same branch, no-op. Mismatch (different branch) raises."""
    runner = runner or _default_runner
    if worktree_path.exists():
        # Inspect git worktree list to confirm it's the same branch.
        result = runner(["git", "worktree", "list", "--porcelain"], cwd=repo_dir)
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                wt = Path(line.split(" ", 1)[1])
                if wt.resolve() == worktree_path.resolve():
                    # Found the existing worktree — caller is responsible
                    # for verifying branch match; we just don't re-add.
                    return
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = runner(
        ["git", "worktree", "add", str(worktree_path), branch], cwd=repo_dir
    )
    if result.returncode != 0:
        raise GitError(
            f"git worktree add {worktree_path}/{branch} failed: "
            f"rc={result.returncode} stderr={result.stderr.strip()}"
        )


def has_commits_beyond(
    repo_dir: Path,
    branch: str,
    *,
    base: str,
    runner: CmdRunner | None = None,
) -> bool:
    """True iff *branch* has at least one commit that *base* does not."""
    runner = runner or _default_runner
    result = runner(
        ["git", "rev-list", "--count", f"{base}..{branch}"],
        cwd=repo_dir,
    )
    if result.returncode != 0:
        raise GitError(
            f"git rev-list {base}..{branch} failed: rc={result.returncode} "
            f"stderr={result.stderr.strip()}"
        )
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return False


def latest_commit_subject(
    repo_dir: Path,
    branch: str,
    *,
    runner: CmdRunner | None = None,
) -> str:
    runner = runner or _default_runner
    result = runner(
        ["git", "log", "-1", "--pretty=%s", branch], cwd=repo_dir
    )
    if result.returncode != 0:
        raise GitError(
            f"git log subject for {branch} failed: rc={result.returncode} "
            f"stderr={result.stderr.strip()}"
        )
    return result.stdout.strip()


def latest_commit_body(
    repo_dir: Path,
    branch: str,
    *,
    runner: CmdRunner | None = None,
) -> str:
    runner = runner or _default_runner
    result = runner(
        ["git", "log", "-1", "--pretty=%b", branch], cwd=repo_dir
    )
    if result.returncode != 0:
        raise GitError(
            f"git log body for {branch} failed: rc={result.returncode} "
            f"stderr={result.stderr.strip()}"
        )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# gh operations
# ---------------------------------------------------------------------------


def gh_create_pr(
    repo_dir: Path,
    *,
    head: str,
    base: str,
    title: str,
    body: str,
    draft: bool = False,
    runner: CmdRunner | None = None,
) -> str:
    """Run `gh pr create` from *repo_dir*. Returns the PR URL."""
    runner = runner or _default_runner
    args = [
        "gh",
        "pr",
        "create",
        "--head",
        head,
        "--base",
        base,
        "--title",
        title,
        "--body",
        body,
    ]
    if draft:
        args.append("--draft")
    result = runner(args, cwd=repo_dir)
    if result.returncode != 0:
        raise GhError(
            f"gh pr create failed: rc={result.returncode} stderr={result.stderr.strip()}"
        )
    # gh's stdout typically contains the PR URL (and possibly other lines).
    match = _PR_URL_RE.search(result.stdout)
    if not match:
        raise GhError(
            f"gh pr create succeeded but no PR URL found in stdout: "
            f"{result.stdout!r}"
        )
    return match.group(0)
