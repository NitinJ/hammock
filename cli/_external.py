"""Thin wrappers around external CLIs (``git``, ``gh``).

These functions exist as a single seam so tests can monkey-patch them. Each
runs a subprocess and returns a typed result; failures are caught and
surfaced as the documented sentinel (``None`` / ``False``) rather than
raising. The CLI command layer interprets these.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(
    cmd: list[str], *, cwd: Path | None = None, timeout: float = 5.0
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else None,
        timeout=timeout,
        check=False,
    )


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------


def git_remote_url(repo: Path) -> str | None:
    """Return ``origin``'s URL, or ``None`` if not configured / not a repo."""
    res = _run(["git", "remote", "get-url", "origin"], cwd=repo)
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def git_default_branch(repo: Path) -> str | None:
    """Detect remote ``HEAD`` (default branch). ``None`` if undetectable."""
    res = _run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo)
    if res.returncode == 0:
        ref = res.stdout.strip()
        # 'refs/remotes/origin/main' → 'main'
        if "/" in ref:
            return ref.rsplit("/", 1)[-1]
    # Fallback: probe common branch names
    for candidate in ("main", "master"):
        probe = _run(
            ["git", "show-ref", "--verify", f"refs/remotes/origin/{candidate}"],
            cwd=repo,
        )
        if probe.returncode == 0:
            return candidate
    return None


def git_working_tree_dirty(repo: Path) -> bool:
    """Return True if there are uncommitted changes."""
    res = _run(["git", "status", "--porcelain"], cwd=repo)
    if res.returncode != 0:
        return False
    return bool(res.stdout.strip())


def git_is_repo(path: Path) -> bool:
    """True iff *path* contains a ``.git`` directory or file."""
    return (path / ".git").exists()


# ---------------------------------------------------------------------------
# gh
# ---------------------------------------------------------------------------


def gh_auth_ok() -> bool:
    """True iff ``gh auth status`` reports an authenticated session."""
    res = _run(["gh", "auth", "status"], timeout=10.0)
    return res.returncode == 0


def gh_repo_view(remote_url: str) -> bool:
    """True iff the remote is reachable + the user has access."""
    res = _run(["gh", "repo", "view", remote_url], timeout=15.0)
    return res.returncode == 0
