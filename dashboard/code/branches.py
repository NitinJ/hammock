"""Git branch lifecycle wrappers — Hammock owns the ``hammock/...`` namespace.

Per `docs/v0-alignment-report.md` Plan #2 + #8 (paired): Hammock creates a
job branch on job submit and a stage branch per stage attempt. All
branches live under the ``hammock/`` prefix so they're greppable in
`git branch` and easy to clean up safely.

**Branch naming.** Two disjoint sub-namespaces are used because git's
ref tree cannot have both a branch ``hammock/x`` and a branch
``hammock/x/y`` (the on-disk layout would need ``refs/heads/hammock/x``
to be both a file and a directory):

- Job branch:   ``hammock/jobs/<job_slug>``
- Stage branch: ``hammock/stages/<job_slug>/<stage_id>``

Agent0 still owns ``git push`` and ``gh pr create`` per design
(`docs/design.md:3275-3289` and `docs/design.md:3324-3335`); this module
only handles branch creation/deletion against the local repo.

Safety rails:

- ``delete_branch`` refuses any branch not under ``hammock/`` so the
  helper can never accidentally delete the operator's working
  branches.
- All operations are idempotent: re-running a submit / re-spawning a
  driver after a crash must not raise on existing branches.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

NAMESPACE = "hammock/"
JOB_PREFIX = "hammock/jobs/"
STAGE_PREFIX = "hammock/stages/"


class BranchNotFoundError(Exception):
    """Raised when a branch the helper expected to find doesn't exist."""


class BranchExistsError(Exception):
    """Reserved for callers that explicitly require a fresh branch."""


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command against *repo*; capture stdout/stderr; check=True."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _job_branch(job_slug: str) -> str:
    return f"{JOB_PREFIX}{job_slug}"


def _stage_branch(job_slug: str, stage_id: str) -> str:
    return f"{STAGE_PREFIX}{job_slug}/{stage_id}"


def branch_exists(repo: Path, branch: str) -> bool:
    """True iff *branch* is a local ref in *repo*."""
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
        capture_output=True,
    )
    return result.returncode == 0


def list_hammock_branches(repo: Path) -> list[str]:
    """Every local branch under the ``hammock/`` namespace."""
    out = _git(
        repo,
        "for-each-ref",
        "--format=%(refname:short)",
        f"refs/heads/{NAMESPACE}",
    )
    return sorted(line.strip() for line in out.stdout.splitlines() if line.strip())


def create_job_branch(repo: Path, job_slug: str, *, base: str = "main") -> str:
    """Create ``hammock/<job_slug>`` off *base*. Idempotent.

    If the branch already exists, returns its name without modifying it
    — preserving any work that was committed on a prior submit attempt.
    Callers that need a fresh branch should ``delete_branch`` first.
    """
    branch = _job_branch(job_slug)
    if branch_exists(repo, branch):
        return branch
    _git(repo, "branch", branch, base)
    return branch


def create_stage_branch(
    repo: Path,
    job_slug: str,
    stage_id: str,
    *,
    parent: str | None = None,
) -> str:
    """Create ``hammock/<job_slug>/<stage_id>`` off the job branch
    (or *parent* if supplied). Idempotent.

    Raises :class:`BranchNotFoundError` if the parent branch doesn't
    exist — that's a wiring bug (``create_job_branch`` was skipped).
    """
    parent_branch = parent or _job_branch(job_slug)
    if not branch_exists(repo, parent_branch):
        raise BranchNotFoundError(
            f"parent branch {parent_branch!r} does not exist; "
            "create_job_branch must be called first"
        )
    branch = _stage_branch(job_slug, stage_id)
    if branch_exists(repo, branch):
        return branch
    _git(repo, "branch", branch, parent_branch)
    return branch


def delete_branch(repo: Path, branch: str, *, force: bool = False) -> None:
    """Delete a local branch under ``hammock/``.

    Refuses any branch not in the ``hammock/`` namespace as a safety
    rail. Set ``force=True`` to silently swallow "branch not found"
    (useful for cleanup of already-removed branches on resume).
    """
    if not branch.startswith(NAMESPACE):
        raise ValueError(
            f"refusing to delete {branch!r}: only branches under "
            f"{NAMESPACE!r} can be deleted via this helper"
        )
    if not branch_exists(repo, branch):
        if force:
            return
        raise BranchNotFoundError(f"branch {branch!r} does not exist in {repo}")
    # -D is force-delete (works even if not merged into HEAD); we already
    # confirmed the branch is under our namespace so the safety case is
    # the namespace check above, not the merge check.
    _git(repo, "branch", "-D", branch)
