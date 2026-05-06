"""Substrate allocator for `code`-kind nodes.

Per design-patch §2.4. Owns the branch hierarchy (main → job branch →
stage branch) and the per-code-node worktree. Pulls the job branch
from origin before each fork so subsequent stage branches see prior
merges.

Three entry points:

- :func:`copy_local_repo` — at job submit, copy the operator's
  registered project directory (``project.repo_path``) into
  ``<job_dir>/repo`` (full ``cp -R``: tracked + untracked + ``.env`` +
  ``.git``), check out ``default_branch``, create the job branch off
  it, push to origin. Replaces the older clone-from-URL flow.
- :func:`set_up_job_repo` — DEPRECATED clone-from-URL flow; kept on
  the module while call sites migrate. Will be deleted when no
  callers remain.
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


def copy_local_repo(
    *,
    job_slug: str,
    root: Path,
    repo_path: Path,
    repo_slug: str,
    default_branch: str,
    runner: git_ops.CmdRunner | None = None,
) -> JobRepo:
    """Copy the operator's registered local checkout into ``<job_dir>/repo``
    and prepare the job branch.

    Per ``docs/projects-management.md``:

    1. ``cp -R <repo_path>/. <job_dir>/repo/`` — full copy: tracked,
       untracked, ``.env``, ``.git``. The operator's working tree is
       read-only to Hammock; we never write back.
    2. ``git checkout <default_branch>`` inside the copy — the operator's
       current HEAD does NOT travel; the job branches off the project's
       default branch regardless of where the operator was working.
    3. Create ``hammock/jobs/<slug>`` off ``default_branch`` and push
       to origin (preserved by the copy of ``.git/config``).

    Idempotent: a second call with the same ``job_slug`` is a no-op
    when ``<job_dir>/repo`` already exists with the right job branch.
    """
    import shutil

    if not repo_path.exists():
        raise SubstrateError(f"repo_path {repo_path} does not exist; cannot copy")
    if not (repo_path / ".git").exists():
        raise SubstrateError(f"repo_path {repo_path} is not a git repo (no .git/ found)")

    repo_dir = paths.repo_clone_dir(job_slug, root=root)
    job_branch = paths.job_branch_name(job_slug)

    # Idempotent: skip the copy when <job_dir>/repo already exists.
    if not repo_dir.exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(repo_path, repo_dir, symlinks=True)
        except OSError as exc:
            raise SubstrateError(f"could not copy {repo_path} → {repo_dir}: {exc}") from exc

        # Check out the project's default branch so the job branch forks
        # off it regardless of the operator's current HEAD. If the local
        # copy doesn't have the branch (e.g. operator was working on a
        # feature branch and never fetched main), git surfaces a clear
        # error and we propagate.
        active_runner = runner or git_ops._default_runner  # type: ignore[attr-defined]
        checkout = active_runner(["git", "checkout", default_branch], cwd=repo_dir, check=False)
        if checkout.returncode != 0:
            raise SubstrateError(
                f"could not check out {default_branch!r} in {repo_dir}: {checkout.stderr.strip()}"
            )

    # Job branch creation + push (idempotent — both checks return early
    # when the branch is already present locally / on the remote).
    if not git_ops.branch_exists_local(repo_dir, job_branch, runner=runner):
        try:
            git_ops.create_branch(repo_dir, job_branch, default_branch, runner=runner)
        except git_ops.GitError as exc:
            raise SubstrateError(f"could not create job branch {job_branch!r}: {exc}") from exc

    if not git_ops.branch_exists_remote(repo_dir, job_branch, runner=runner):
        try:
            git_ops.push_branch(repo_dir, job_branch, runner=runner)
        except git_ops.GitError as exc:
            raise SubstrateError(
                f"could not push job branch {job_branch!r} to origin: {exc}"
            ) from exc

    return JobRepo(repo_dir=repo_dir, repo_slug=repo_slug, job_branch=job_branch)


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
            git_ops.create_branch(repo_dir, job_branch, f"origin/{base}", runner=runner)
        except git_ops.GitError as exc:
            raise SubstrateError(f"could not create job branch {job_branch!r}: {exc}") from exc

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
    git_ops.update_local_branch_to_remote(job_repo.repo_dir, job_repo.job_branch, runner=runner)

    if not git_ops.branch_exists_local(job_repo.repo_dir, stage_branch, runner=runner):
        # Fork off the freshly-fetched job branch.
        git_ops.create_branch(
            job_repo.repo_dir,
            stage_branch,
            f"origin/{job_repo.job_branch}",
            runner=runner,
        )

    git_ops.add_worktree(job_repo.repo_dir, worktree, stage_branch, runner=runner)

    return CodeSubstrate(
        repo_dir=job_repo.repo_dir,
        worktree=worktree,
        stage_branch=stage_branch,
        base_branch=job_repo.job_branch,
        repo_slug=job_repo.repo_slug,
    )
