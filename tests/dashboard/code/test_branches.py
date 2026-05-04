"""Tests for `dashboard.code.branches` — git branch lifecycle wrappers.

Per `docs/v0-alignment-report.md` Plan #2 + #8 (paired): Hammock owns
job and stage branches. The shape:

- ``hammock/<job_slug>``                — created at job submit, off the
                                          project's default branch.
- ``hammock/<job_slug>/<stage_id>``     — created at stage start, off
                                          the job branch.
- All branches under the ``hammock/`` namespace are greppable for
  forensics and cleanup.

Branch deletion follows a simple rule: workspaces (worktrees) are torn
down on terminal stage state, but branches stay on disk for forensics
unless the caller explicitly asks for deletion.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dashboard.code.branches import (
    BranchNotFoundError,
    branch_exists,
    create_job_branch,
    create_stage_branch,
    delete_branch,
    list_hammock_branches,
)


def _init_repo(path: Path) -> Path:
    """Initialise a fresh git repo with one commit on `main`."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"], cwd=path, check=True, capture_output=True
    )
    (path / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def _list_branches(repo: Path) -> set[str]:
    out = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def _current_branch(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


# ---------------------------------------------------------------------------
# create_job_branch
# ---------------------------------------------------------------------------


def test_create_job_branch_makes_a_namespaced_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    branch = create_job_branch(repo, "j1", base="main")
    assert branch == "hammock/jobs/j1"
    assert "hammock/jobs/j1" in _list_branches(repo)
    # We don't switch to it — job branch is a parent ref only.
    assert _current_branch(repo) == "main"


def test_create_job_branch_is_idempotent_when_already_exists(tmp_path: Path) -> None:
    """Re-running submit on the same slug must not raise — the branch
    might exist from a prior aborted submit. Returns the same name."""
    repo = _init_repo(tmp_path / "repo")
    create_job_branch(repo, "j1", base="main")
    branch = create_job_branch(repo, "j1", base="main")
    assert branch == "hammock/jobs/j1"


def test_create_job_branch_rejects_non_hammock_slug_collision(tmp_path: Path) -> None:
    """If a branch named `hammock/<slug>` exists but doesn't point where
    the job branch should — i.e. base has moved — that's a collision the
    caller must resolve. The current implementation reuses any existing
    `hammock/<slug>`; this test pins the policy as 'reuse, do not move'."""
    repo = _init_repo(tmp_path / "repo")
    # Pre-create a branch at a different commit
    subprocess.run(
        ["git", "checkout", "-b", "hammock/jobs/j2"], cwd=repo, check=True, capture_output=True
    )
    (repo / "extra.txt").write_text("x\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "extra"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "main"], cwd=repo, check=True, capture_output=True)

    # Submit reuses the existing branch — does not "move" it back to base.
    branch = create_job_branch(repo, "j2", base="main")
    assert branch == "hammock/jobs/j2"
    # Verify the branch tip is still the extra commit, not main.
    tip = subprocess.run(
        ["git", "rev-parse", "hammock/jobs/j2"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    main_tip = subprocess.run(
        ["git", "rev-parse", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tip != main_tip


# ---------------------------------------------------------------------------
# create_stage_branch
# ---------------------------------------------------------------------------


def test_create_stage_branch_off_job_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    create_job_branch(repo, "j1", base="main")
    branch = create_stage_branch(repo, "j1", "design")
    assert branch == "hammock/stages/j1/design"
    assert branch in _list_branches(repo)


def test_create_stage_branch_idempotent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    create_job_branch(repo, "j1", base="main")
    a = create_stage_branch(repo, "j1", "design")
    b = create_stage_branch(repo, "j1", "design")
    assert a == b == "hammock/stages/j1/design"


def test_create_stage_branch_requires_job_branch_to_exist(tmp_path: Path) -> None:
    """Calling create_stage_branch before create_job_branch is a bug;
    raise BranchNotFoundError so the caller fails fast."""
    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(BranchNotFoundError):
        create_stage_branch(repo, "no-such-job", "design")


# ---------------------------------------------------------------------------
# delete_branch
# ---------------------------------------------------------------------------


def test_delete_branch_removes_it(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    create_job_branch(repo, "j1", base="main")
    create_stage_branch(repo, "j1", "s")
    delete_branch(repo, "hammock/stages/j1/s")
    assert "hammock/stages/j1/s" not in _list_branches(repo)


def test_delete_branch_missing_raises_unless_force(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(BranchNotFoundError):
        delete_branch(repo, "hammock/jobs/no-such-branch")
    # force=True swallows missing
    delete_branch(repo, "hammock/jobs/no-such-branch", force=True)


def test_delete_branch_refuses_branches_outside_hammock_namespace(tmp_path: Path) -> None:
    """Safety rail: only `hammock/...` branches can be deleted via this
    helper. Hammock must never delete a user's `main` or feature
    branches."""
    repo = _init_repo(tmp_path / "repo")
    with pytest.raises(ValueError, match="hammock/"):
        delete_branch(repo, "main")


# ---------------------------------------------------------------------------
# list + exists
# ---------------------------------------------------------------------------


def test_list_hammock_branches_returns_only_hammock_namespaced(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    create_job_branch(repo, "j1", base="main")
    create_stage_branch(repo, "j1", "design")
    create_stage_branch(repo, "j1", "implement")
    branches = list_hammock_branches(repo)
    assert set(branches) == {
        "hammock/jobs/j1",
        "hammock/stages/j1/design",
        "hammock/stages/j1/implement",
    }
    assert "main" not in branches


def test_branch_exists(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    assert not branch_exists(repo, "hammock/jobs/x")
    create_job_branch(repo, "x", base="main")
    assert branch_exists(repo, "hammock/jobs/x")
