"""Substrate allocator for `code`-kind nodes.

Per design-patch §2.4. Owns the branch hierarchy (main → job branch →
stage branch) and the per-code-node worktree. Pulls the job branch
from origin before each fork so subsequent stage branches see prior
merges.

Two entry points:

- :func:`set_up_job_repo` — at job submit, clone the test repo into
  ``<job_dir>/repo`` and create + push the job branch.
- :func:`allocate_code_substrate` — per code-kind node, create the
  stage branch + worktree and return the runtime substrate the
  dispatcher passes to the agent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from engine.v1 import git_ops
from shared.v1 import paths

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobRepo:
    """Engine's local clone of the test repo. Created at submit time."""

    repo_dir: Path
    repo_slug: str
    job_branch: str


@dataclass(frozen=True)
class CodeSubstrate:
    """Runtime substrate for a code-kind node."""

    repo_dir: Path
    """Engine's working clone (parent for all stage worktrees)."""

    worktree: Path
    """Per-stage worktree the agent operates in."""

    stage_branch: str
    """``hammock/stages/<slug>/<node-id>``."""

    base_branch: str
    """``hammock/jobs/<slug>``."""

    repo_slug: str


class SubstrateError(Exception):
    """Raised when substrate setup fails."""


def set_up_job_repo(
    *,
    job_slug: str,
    root: Path,
    repo_url: str,
    repo_slug: str,
    runner: git_ops.CmdRunner | None = None,
    base: str = "main",
) -> JobRepo:
    """Clone the test repo into the job dir and create + push the job
    branch (``hammock/jobs/<slug>``) off ``main``.

    Idempotent: if the clone is already there, just refresh main and
    confirm the job branch exists locally.
    """
    repo_dir = paths.repo_clone_dir(job_slug, root=root)
    git_ops.clone_repo(repo_url, repo_dir, runner=runner)
    git_ops.fetch(repo_dir, ref=base, runner=runner)

    job_branch = paths.job_branch_name(job_slug)
    if not git_ops.branch_exists_local(repo_dir, job_branch, runner=runner):
        # Off origin/<base> — the freshly-fetched ref.
        try:
            git_ops.create_branch(
                repo_dir, job_branch, f"origin/{base}", runner=runner
            )
        except git_ops.GitError as exc:
            raise SubstrateError(
                f"could not create job branch {job_branch!r}: {exc}"
            ) from exc

    # Push job branch best-effort. If push fails (e.g. branch already on
    # remote), we surface but don't crash since job branch existence on
    # remote is the contract — log and continue.
    if not git_ops.branch_exists_remote(repo_dir, job_branch, runner=runner):
        try:
            git_ops.push_branch(repo_dir, job_branch, runner=runner)
        except git_ops.GitError as exc:
            raise SubstrateError(
                f"could not push job branch {job_branch!r} to origin: {exc}"
            ) from exc

    return JobRepo(repo_dir=repo_dir, repo_slug=repo_slug, job_branch=job_branch)


def allocate_code_substrate(
    *,
    job_slug: str,
    node_id: str,
    root: Path,
    job_repo: JobRepo,
    runner: git_ops.CmdRunner | None = None,
) -> CodeSubstrate:
    """Create the stage branch off the job branch + add a worktree.

    Pulls origin/<job_branch> first so the new fork picks up any prior
    work merged into the job branch (per design-patch §2.4)."""
    stage_branch = paths.stage_branch_name(job_slug, node_id)
    worktree = paths.node_worktree_dir(job_slug, node_id, root=root)

    git_ops.fetch(job_repo.repo_dir, ref=job_repo.job_branch, runner=runner)
    # Fast-forward local <job_branch> to origin/<job_branch> so that
    # subsequent ``rev-list job_branch..stage_branch`` checks (used by
    # ``has_commits_beyond`` in pr.produce) give the correct count
    # after prior stage PRs have merged. Without this, the local ref is
    # frozen at clone time and reports stale "phantom" commits on
    # freshly-forked stage branches that haven't been touched by the
    # agent.
    git_ops.update_local_branch_to_remote(
        job_repo.repo_dir, job_repo.job_branch, runner=runner
    )

    if not git_ops.branch_exists_local(
        job_repo.repo_dir, stage_branch, runner=runner
    ):
        # Fork off the freshly-fetched job branch.
        git_ops.create_branch(
            job_repo.repo_dir,
            stage_branch,
            f"origin/{job_repo.job_branch}",
            runner=runner,
        )

    git_ops.add_worktree(
        job_repo.repo_dir, worktree, stage_branch, runner=runner
    )

    return CodeSubstrate(
        repo_dir=job_repo.repo_dir,
        worktree=worktree,
        stage_branch=stage_branch,
        base_branch=job_repo.job_branch,
        repo_slug=job_repo.repo_slug,
    )
