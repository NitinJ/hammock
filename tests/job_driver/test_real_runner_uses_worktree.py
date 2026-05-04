"""Per `docs/v0-alignment-report.md` Plan #2 + #8 (paired):
RealStageRunner must run claude inside the per-stage worktree (when
the JobDriver has set one up) — not the project root. This is what
gives parallel stages real isolation.

Discovery convention: the worktree lives at
``<stage_run_dir>.parent.parent / "worktree"`` — i.e.
``<job_dir>/stages/<sid>/worktree``. If that path exists, use it as
cwd; otherwise fall back to ``project_root``.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from job_driver.stage_runner import RealStageRunner
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    StageDefinition,
)


def _stage(stage_id: str = "s") -> StageDefinition:
    return StageDefinition(
        id=stage_id,
        worker="agent",
        agent_ref="x",
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=[]),
        budget=Budget(max_turns=5),
        exit_condition=ExitCondition(),
    )


FIXTURES = Path(__file__).parent.parent / "fixtures" / "recorded-streams"


def _argv_capture_claude(tmp_path: Path) -> tuple[Path, Path]:
    """Fake claude that records its CWD (via `pwd`) to a sentinel file."""
    cwd_file = tmp_path / "claude-cwd.txt"
    fixture = FIXTURES / "simple_success.jsonl"
    script = tmp_path / "fake_pwd_claude"
    script.write_text(f"#!/usr/bin/env bash\npwd > {cwd_file}\ncat {fixture}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, cwd_file


def _make_stage_run_dir(tmp_path: Path, *, slug: str = "j", sid: str = "s") -> Path:
    """Mirror the path layout the JobDriver creates."""
    d = tmp_path / "hammock-root" / "jobs" / slug / "stages" / sid / "run-1"
    d.mkdir(parents=True)
    return d


def _make_worktree(tmp_path: Path, *, slug: str = "j", sid: str = "s") -> Path:
    """Real (init'd) git worktree-shaped dir at the convention path.

    For the cwd test we don't need a real `git worktree`, just a
    directory at the expected path that Hammock would have created.
    """
    wt = tmp_path / "hammock-root" / "jobs" / slug / "stages" / sid / "worktree"
    wt.mkdir(parents=True, exist_ok=True)
    # Initialise so `git -C <wt> ...` would work; agent code may need it.
    subprocess.run(["git", "init"], cwd=wt, check=True, capture_output=True)
    return wt


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_real_runner_uses_worktree_when_present(tmp_path: Path) -> None:
    """When the convention path exists, claude is invoked with cwd = worktree."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = _make_stage_run_dir(tmp_path)
    worktree = _make_worktree(tmp_path)

    fake_claude, cwd_file = _argv_capture_claude(tmp_path)
    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(_stage(), stage_run_dir.parent.parent.parent.parent, stage_run_dir)

    recorded_cwd = Path(cwd_file.read_text().strip())
    assert recorded_cwd.resolve() == worktree.resolve(), (
        f"claude cwd was {recorded_cwd} but should have been the worktree {worktree}"
    )


async def test_real_runner_falls_back_to_project_root_when_no_worktree(tmp_path: Path) -> None:
    """Without a worktree at the convention path, runner uses project_root."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = _make_stage_run_dir(tmp_path)
    # Note: no _make_worktree call → convention path does not exist.

    fake_claude, cwd_file = _argv_capture_claude(tmp_path)
    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(_stage(), stage_run_dir.parent.parent.parent.parent, stage_run_dir)

    recorded_cwd = Path(cwd_file.read_text().strip())
    assert recorded_cwd.resolve() == project_root.resolve(), (
        f"claude cwd was {recorded_cwd}; expected project_root {project_root} as fallback"
    )
