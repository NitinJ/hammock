"""Tests for `dashboard.code.worktrees` — git worktree lifecycle wrappers.

Per `docs/v0-alignment-report.md` Plan #2 + #8 (paired): per stage
attempt, Hammock checks out the stage branch into a worktree under
``~/.hammock/jobs/<slug>/stages/<sid>/worktree/``. Stage isolation
means two parallel jobs against the same project don't see each
other's edits.

The worktree path lives under the hammock root, *not* under the
project repo's ``.git/worktrees/``, so the project tree stays clean
of Hammock bookkeeping.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dashboard.code.branches import create_job_branch, create_stage_branch
from dashboard.code.worktrees import (
    WorktreeExistsError,
    WorktreeNotFoundError,
    add_worktree,
    list_worktrees,
    remove_worktree,
)


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def _seed_with_stage_branch(tmp_path: Path) -> tuple[Path, Path]:
    """Repo + a job + stage branch ready for worktree checkout.

    The returned worktree path matches Hammock's runtime layout
    (``.../jobs/<slug>/stages/<sid>/worktree``) so the safety rail in
    ``remove_worktree`` accepts it.
    """
    repo = _init_repo(tmp_path / "repo")
    create_job_branch(repo, "j1", base="main")
    create_stage_branch(repo, "j1", "design")
    wt = tmp_path / "hammock-root" / "jobs" / "j1" / "stages" / "design" / "worktree"
    return repo, wt


# ---------------------------------------------------------------------------
# add_worktree
# ---------------------------------------------------------------------------


def test_add_worktree_creates_directory_with_branch_checked_out(tmp_path: Path) -> None:
    repo, wt = _seed_with_stage_branch(tmp_path)
    add_worktree(repo, wt, "hammock/stages/j1/design")
    assert wt.is_dir()
    # The README.md from the initial commit should be present (worktree
    # is a real checkout).
    assert (wt / "README.md").is_file()
    # Worktree's HEAD points at the stage branch.
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=wt,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == "hammock/stages/j1/design"


def test_add_worktree_creates_parent_dirs(tmp_path: Path) -> None:
    """Caller may pass a path whose parent doesn't exist yet."""
    repo, _ = _seed_with_stage_branch(tmp_path)
    deep = tmp_path / "deep" / "hammock-root" / "jobs" / "j1" / "stages" / "design" / "worktree"
    add_worktree(repo, deep, "hammock/stages/j1/design")
    assert deep.is_dir()


def test_add_worktree_rejects_already_existing_worktree(tmp_path: Path) -> None:
    repo, wt = _seed_with_stage_branch(tmp_path)
    add_worktree(repo, wt, "hammock/stages/j1/design")
    with pytest.raises(WorktreeExistsError):
        add_worktree(repo, wt, "hammock/stages/j1/design")


def test_add_worktree_idempotent_with_reuse_flag(tmp_path: Path) -> None:
    """JobDriver resume needs to be able to re-discover an existing
    worktree without erroring. ``reuse_existing=True`` makes
    add_worktree a no-op when the path is already a registered
    worktree for the same branch."""
    repo, wt = _seed_with_stage_branch(tmp_path)
    add_worktree(repo, wt, "hammock/stages/j1/design")
    # Should not raise:
    add_worktree(repo, wt, "hammock/stages/j1/design", reuse_existing=True)
    assert wt.is_dir()


def test_add_worktree_reuse_flag_rejects_branch_mismatch(tmp_path: Path) -> None:
    """If the existing worktree is checked out to a *different* branch
    than the caller asked for, that's a real bug — fail loudly even
    with reuse_existing=True."""
    repo, wt = _seed_with_stage_branch(tmp_path)
    create_stage_branch(repo, "j1", "implement")
    add_worktree(repo, wt, "hammock/stages/j1/design")
    with pytest.raises(WorktreeExistsError, match="different branch"):
        add_worktree(repo, wt, "hammock/stages/j1/implement", reuse_existing=True)


# ---------------------------------------------------------------------------
# remove_worktree
# ---------------------------------------------------------------------------


def test_remove_worktree_unregisters_and_deletes_dir(tmp_path: Path) -> None:
    repo, wt = _seed_with_stage_branch(tmp_path)
    add_worktree(repo, wt, "hammock/stages/j1/design")
    remove_worktree(repo, wt)
    assert not wt.exists()
    # Git no longer lists it.
    assert wt not in [w["path"] for w in list_worktrees(repo)]


def test_remove_worktree_force_handles_dirty_tree(tmp_path: Path) -> None:
    """A worktree with uncommitted edits must still be removable on
    cleanup (the agent's edits are committed-or-lost-by-design after
    a failed stage; we don't preserve uncommitted forensic state)."""
    repo, wt = _seed_with_stage_branch(tmp_path)
    add_worktree(repo, wt, "hammock/stages/j1/design")
    (wt / "scratch.txt").write_text("dirty\n")
    remove_worktree(repo, wt)  # default = force
    assert not wt.exists()


def test_remove_worktree_force_false_propagates_dirty_refusal(tmp_path: Path) -> None:
    """Codex review of PR #24: ``force=False`` must actually honour
    the flag. Earlier code suppressed the CalledProcessError and then
    rmtree'd anyway, defeating the safety knob entirely."""
    repo, wt = _seed_with_stage_branch(tmp_path)
    add_worktree(repo, wt, "hammock/stages/j1/design")
    (wt / "scratch.txt").write_text("dirty\n")

    with pytest.raises(subprocess.CalledProcessError):
        remove_worktree(repo, wt, force=False)
    # And the directory survives the refusal.
    assert wt.exists()
    assert (wt / "scratch.txt").exists()


def test_remove_worktree_missing_raises_unless_missing_ok(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    nonexistent = tmp_path / "hammock-root" / "jobs" / "ghost" / "stages" / "g" / "worktree"
    with pytest.raises(WorktreeNotFoundError):
        remove_worktree(repo, nonexistent)
    # missing_ok=True swallows
    remove_worktree(repo, nonexistent, missing_ok=True)


def test_remove_worktree_refuses_path_outside_hammock_layout(tmp_path: Path) -> None:
    """Safety rail mirroring branches.delete_branch: only
    Hammock-managed paths can be removed via this helper. Concretely,
    the path must include a ``stages/<id>/worktree`` segment so we
    never call `git worktree remove` on the operator's project tree."""
    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(ValueError, match="hammock-managed"):
        remove_worktree(repo, repo)


# ---------------------------------------------------------------------------
# list_worktrees
# ---------------------------------------------------------------------------


def test_list_worktrees_includes_added_worktree(tmp_path: Path) -> None:
    repo, wt = _seed_with_stage_branch(tmp_path)
    add_worktree(repo, wt, "hammock/stages/j1/design")
    entries = list_worktrees(repo)
    paths = [e["path"] for e in entries]
    branches = [e.get("branch") for e in entries]
    assert wt in paths
    assert "hammock/stages/j1/design" in branches
