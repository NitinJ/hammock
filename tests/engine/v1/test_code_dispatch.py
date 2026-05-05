"""Unit tests for engine/v1/code_dispatch.py.

Inject fake `claude_runner` and patch git_ops calls (used inside the
post-actor `produce`) so unit tests don't need real git/gh.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.v1 import git_ops
from engine.v1.code_dispatch import dispatch_code_agent
from engine.v1.substrate import CodeSubstrate
from shared.v1 import paths
from shared.v1.envelope import Envelope, make_envelope
from shared.v1.workflow import CodeNode, VariableSpec, Workflow


def _seed_var(
    *, root: Path, job_slug: str, var_name: str, type_name: str, value: dict
) -> None:
    paths.ensure_job_layout(job_slug, root=root)
    env = make_envelope(
        type_name=type_name, producer_node="<test>", value_payload=value
    )
    paths.variable_envelope_path(job_slug, var_name, root=root).write_text(
        env.model_dump_json()
    )


def _t3_workflow() -> Workflow:
    return Workflow(
        workflow="t3",
        variables={
            "request": VariableSpec(type="job-request"),
            "design_spec": VariableSpec(type="design-spec"),
            "pr": VariableSpec(type="pr"),
        },
        nodes=[
            CodeNode(
                id="implement",
                kind="code",
                actor="agent",
                inputs={"design_spec": "$design_spec"},
                outputs={"pr": "$pr"},
            )
        ],
    )


def _make_substrate(tmp_path: Path) -> CodeSubstrate:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    return CodeSubstrate(
        repo_dir=repo_dir,
        worktree=worktree,
        stage_branch="hammock/stages/j/implement",
        base_branch="hammock/jobs/j",
        repo_slug="me/repo",
    )


# ---------------------------------------------------------------------------
# Happy path: agent commits, engine push + opens PR via produce.
# ---------------------------------------------------------------------------


def test_dispatch_code_happy_path(tmp_path: Path) -> None:
    job_slug = "j"
    _seed_var(
        root=tmp_path, job_slug=job_slug, var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y"},
    )
    wf = _t3_workflow()
    substrate = _make_substrate(tmp_path)

    def fake_claude(
        prompt: str, attempt_dir: Path, worktree: Path
    ) -> subprocess.CompletedProcess[str]:
        # Simulate agent committing.
        (attempt_dir / "stdout.log").write_text("(fake) edited + committed\n")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    # Patch git_ops so produce's pr-type calls don't hit real git/gh.
    with (
        patch.object(git_ops, "has_commits_beyond", return_value=True),
        patch.object(git_ops, "push_branch") as mock_push,
        patch.object(
            git_ops,
            "gh_create_pr",
            return_value="https://github.com/me/repo/pull/9",
        ) as mock_gh,
        patch.object(
            git_ops,
            "latest_commit_subject",
            return_value="fix: empty case",
        ),
        patch.object(
            git_ops,
            "latest_commit_body",
            return_value="body",
        ),
    ):
        result = dispatch_code_agent(
            node=wf.nodes[0],
            workflow=wf,
            job_slug=job_slug,
            root=tmp_path,
            substrate=substrate,
            claude_runner=fake_claude,
        )

    assert result.succeeded
    mock_push.assert_called_once_with(
        substrate.repo_dir, substrate.stage_branch, force=True
    )
    assert mock_gh.called

    # PR envelope persisted.
    env_path = paths.variable_envelope_path(job_slug, "pr", root=tmp_path)
    assert env_path.is_file()
    env = Envelope.model_validate_json(env_path.read_text())
    assert env.type == "pr"
    assert env.value["url"] == "https://github.com/me/repo/pull/9"
    assert env.value["number"] == 9
    assert env.repo == "me/repo"


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------


def test_dispatch_writes_prompt_with_substrate_context(tmp_path: Path) -> None:
    job_slug = "j"
    _seed_var(
        root=tmp_path, job_slug=job_slug, var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y"},
    )
    wf = _t3_workflow()
    substrate = _make_substrate(tmp_path)

    def fake(
        prompt: str, attempt_dir: Path, worktree: Path
    ) -> subprocess.CompletedProcess[str]:
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    with (
        patch.object(git_ops, "has_commits_beyond", return_value=True),
        patch.object(git_ops, "push_branch"),
        patch.object(
            git_ops,
            "gh_create_pr",
            return_value="https://github.com/me/repo/pull/1",
        ),
        patch.object(git_ops, "latest_commit_subject", return_value="t"),
        patch.object(git_ops, "latest_commit_body", return_value="b"),
    ):
        result = dispatch_code_agent(
            node=wf.nodes[0],
            workflow=wf,
            job_slug=job_slug,
            root=tmp_path,
            substrate=substrate,
            claude_runner=fake,
        )

    prompt_text = (result.attempt_dir / "prompt.md").read_text()
    assert "code" in prompt_text.lower()
    assert str(substrate.worktree) in prompt_text
    assert substrate.stage_branch in prompt_text
    assert substrate.base_branch in prompt_text
    # Renders consumer hint for design_spec input.
    assert "design-spec" in prompt_text
    # Renders pr-output instruction (no push from agent).
    assert "Do not run" in prompt_text
    assert "gh pr create" in prompt_text


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_dispatch_fails_when_subprocess_nonzero(tmp_path: Path) -> None:
    job_slug = "j"
    _seed_var(
        root=tmp_path, job_slug=job_slug, var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y"},
    )
    wf = _t3_workflow()
    substrate = _make_substrate(tmp_path)

    def fake(
        prompt: str, attempt_dir: Path, worktree: Path
    ) -> subprocess.CompletedProcess[str]:
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("(fake) crashed\n")
        return subprocess.CompletedProcess(args=["c"], returncode=2, stdout=b"", stderr=b"")

    result = dispatch_code_agent(
        node=wf.nodes[0],
        workflow=wf,
        job_slug=job_slug,
        root=tmp_path,
        substrate=substrate,
        claude_runner=fake,
    )
    assert not result.succeeded
    assert "rc=2" in (result.error or "")


def test_dispatch_fails_when_branch_has_no_commits(tmp_path: Path) -> None:
    """Agent ran but didn't commit any code. PR.produce should fail
    (no commits beyond base) and dispatcher reports failure."""
    job_slug = "j"
    _seed_var(
        root=tmp_path, job_slug=job_slug, var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y"},
    )
    wf = _t3_workflow()
    substrate = _make_substrate(tmp_path)

    def fake(
        prompt: str, attempt_dir: Path, worktree: Path
    ) -> subprocess.CompletedProcess[str]:
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    with patch.object(git_ops, "has_commits_beyond", return_value=False):
        result = dispatch_code_agent(
            node=wf.nodes[0],
            workflow=wf,
            job_slug=job_slug,
            root=tmp_path,
            substrate=substrate,
            claude_runner=fake,
        )
    assert not result.succeeded
    assert "no commits" in (result.error or "")


def test_dispatch_fails_on_gh_error(tmp_path: Path) -> None:
    job_slug = "j"
    _seed_var(
        root=tmp_path, job_slug=job_slug, var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y"},
    )
    wf = _t3_workflow()
    substrate = _make_substrate(tmp_path)

    def fake(
        prompt: str, attempt_dir: Path, worktree: Path
    ) -> subprocess.CompletedProcess[str]:
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    with (
        patch.object(git_ops, "has_commits_beyond", return_value=True),
        patch.object(git_ops, "push_branch"),
        patch.object(
            git_ops,
            "gh_create_pr",
            side_effect=git_ops.GhError("mock failure"),
        ),
        patch.object(git_ops, "latest_commit_subject", return_value="t"),
        patch.object(git_ops, "latest_commit_body", return_value=""),
    ):
        result = dispatch_code_agent(
            node=wf.nodes[0],
            workflow=wf,
            job_slug=job_slug,
            root=tmp_path,
            substrate=substrate,
            claude_runner=fake,
        )
    assert not result.succeeded
    assert "gh error" in (result.error or "").lower()
