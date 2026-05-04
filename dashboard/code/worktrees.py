"""Git worktree lifecycle wrappers — Hammock owns stage worktrees.

Per `docs/v0-alignment-report.md` Plan #2 + #8 (paired): each stage
attempt is checked out into a worktree under
``~/.hammock/jobs/<slug>/stages/<sid>/worktree/``. This is the
isolation layer that lets two parallel jobs against the same project
work without colliding (the shared design summary § "Execution plane"
calls this out).

Boundaries:

- **Task** worktrees are Agent0's responsibility per
  `docs/design.md:3304-3308` — this module deliberately does *not*
  manage them.
- The worktree path lives under the **hammock root**, not under the
  project repo's ``.git/worktrees/``, so the project tree stays clean.
- ``remove_worktree`` refuses paths that aren't part of Hammock's
  documented layout (i.e. don't include the ``stages/<id>/worktree``
  segment) — same safety rail as ``branches.delete_branch``.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
from pathlib import Path
from typing import TypedDict


class WorktreeNotFoundError(Exception):
    pass


class WorktreeExistsError(Exception):
    pass


class WorktreeEntry(TypedDict, total=False):
    path: Path
    branch: str
    head: str
    bare: bool
    detached: bool


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def list_worktrees(repo: Path) -> list[WorktreeEntry]:
    """Parse ``git worktree list --porcelain``."""
    out = _git(repo, "worktree", "list", "--porcelain")
    entries: list[WorktreeEntry] = []
    current: WorktreeEntry = {}
    for raw in out.stdout.splitlines():
        line = raw.rstrip()
        if not line:
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = Path(line[len("worktree ") :]).resolve()
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD ") :]
        elif line.startswith("branch "):
            # `branch refs/heads/<name>` — strip the prefix
            ref = line[len("branch ") :]
            current["branch"] = ref.removeprefix("refs/heads/")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
    if current:
        entries.append(current)
    return entries


def _looks_hammock_managed(path: Path) -> bool:
    """True if *path* looks like a Hammock stage worktree.

    Layout:  .../jobs/<slug>/stages/<sid>/worktree[/...]
    Conservative: refuses anything else so we never touch the
    operator's working tree.
    """
    parts = path.resolve().parts
    if "worktree" not in parts:
        return False
    idx = parts.index("worktree")
    # Need: ..., 'jobs', <slug>, 'stages', <sid>, 'worktree'
    return idx >= 4 and parts[idx - 4] == "jobs" and parts[idx - 2] == "stages"


def add_worktree(
    repo: Path,
    path: Path,
    branch: str,
    *,
    reuse_existing: bool = False,
) -> Path:
    """Check *branch* out into a worktree at *path*.

    Idempotent under ``reuse_existing=True``: if a worktree at *path*
    already exists for the same branch, returns its path. If for a
    different branch, raises (that's a wiring bug).
    """
    path = path.resolve()
    existing = next(
        (e for e in list_worktrees(repo) if e.get("path") == path),
        None,
    )
    if existing is not None:
        if not reuse_existing:
            raise WorktreeExistsError(
                f"worktree already exists at {path}; pass reuse_existing=True to accept"
            )
        if existing.get("branch") != branch:
            raise WorktreeExistsError(
                f"worktree at {path} is on a different branch "
                f"({existing.get('branch')!r}, asked for {branch!r})"
            )
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", str(path), branch)
    return path


def remove_worktree(
    repo: Path,
    path: Path,
    *,
    missing_ok: bool = False,
    force: bool = True,
) -> None:
    """Unregister and delete a Hammock-managed worktree.

    ``force=True`` (the default) lets git remove a worktree with
    uncommitted changes — by design, since a stage that didn't
    succeed loses its uncommitted state on cleanup.

    ``force=False`` makes the call propagate the underlying
    ``CalledProcessError`` if git refuses (e.g. dirty tree); the
    directory is **not** removed in that case.

    Raises :class:`ValueError` if *path* doesn't look like a
    Hammock-managed worktree (safety rail: never delete the operator's
    project tree).
    """
    path = path.resolve()
    if not _looks_hammock_managed(path):
        raise ValueError(f"refusing to remove {path}: not a hammock-managed worktree path")

    registered = any(e.get("path") == path for e in list_worktrees(repo))
    if not registered and not path.exists():
        if missing_ok:
            return
        raise WorktreeNotFoundError(f"no worktree registered at {path}")

    if registered:
        cmd = ["worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(str(path))
        if force:
            # With --force: any git refusal is something we can clean up
            # behind via `shutil.rmtree` below (e.g. directory partially
            # removed externally).
            with contextlib.suppress(subprocess.CalledProcessError):
                _git(repo, *cmd)
        else:
            # Without --force: surface git's refusal (e.g. dirty tree)
            # to the caller and leave the directory in place. Codex
            # review of PR #24 caught that the original implementation
            # silently rmtree'd anyway, defeating the safety knob.
            _git(repo, *cmd)

    if force and path.exists():
        # Belt-and-braces: ensure the directory is gone even if `git
        # worktree remove --force` left bits behind. Only runs in the
        # force path; the not-force path leaves the dir alone after a
        # git refusal.
        shutil.rmtree(path, ignore_errors=True)

    # Always run prune so the registry stays clean even after manual
    # rmtree paths.
    _git(repo, "worktree", "prune")
