"""Tests for RealStageRunner.

Uses a fake claude binary (a shell script that cats a recorded stream fixture)
to exercise the subprocess-spawn, stream-capture, and extraction path without
requiring a real Claude Code installation or API key.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from job_driver.stage_runner import RealStageRunner
from shared.models.stage import Budget, ExitCondition, StageDefinition

FIXTURES = Path(__file__).parent.parent / "fixtures" / "recorded-streams"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stage(
    stage_id: str = "write-problem-spec",
    description: str = "Write the problem specification.",
    required_outputs: list[str] | None = None,
) -> StageDefinition:
    from shared.models.stage import RequiredOutput

    ros = [RequiredOutput(path=p) for p in required_outputs] if required_outputs else None
    return StageDefinition(
        id=stage_id,
        description=description,
        worker="agent",
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(required_outputs=ros),
    )


def _write_fake_claude(tmp_path: Path, fixture_name: str) -> Path:
    """Write a shell script that acts as the claude binary.

    Prints the contents of a recorded fixture to stdout and exits 0.
    """
    fixture_path = FIXTURES / fixture_name
    script = tmp_path / "fake_claude"
    script.write_text(f"#!/usr/bin/env bash\ncat {fixture_path}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


def _write_fake_claude_exitcode(tmp_path: Path, fixture_name: str, exit_code: int) -> Path:
    """Fake claude that emits fixture content but exits with exit_code."""
    fixture_path = FIXTURES / fixture_name
    script = tmp_path / "fake_claude_err"
    script.write_text(f"#!/usr/bin/env bash\ncat {fixture_path}\nexit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return script


# ---------------------------------------------------------------------------
# subprocess spawning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_jsonl_written_from_subprocess_stdout(tmp_path: Path) -> None:
    """Subprocess stdout is captured line-by-line into stream.jsonl."""
    fake_claude = _write_fake_claude(tmp_path, "simple_success.jsonl")
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    runner = RealStageRunner(
        project_root=project_root,
        claude_binary=str(fake_claude),
    )
    stage_def = _make_stage()
    await runner.run(stage_def, tmp_path, stage_run_dir)

    stream_path = stage_run_dir / "agent0" / "stream.jsonl"
    assert stream_path.exists(), "stream.jsonl must be written"
    lines = [json.loads(ln) for ln in stream_path.read_text().splitlines() if ln.strip()]
    types = [ln["type"] for ln in lines]
    assert "system" in types
    assert "result" in types


@pytest.mark.asyncio
async def test_success_result_maps_to_stage_result_succeeded(tmp_path: Path) -> None:
    """A fixture with is_error=false → StageResult.succeeded=True."""
    fake_claude = _write_fake_claude(tmp_path, "simple_success.jsonl")
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    runner = RealStageRunner(
        project_root=project_root,
        claude_binary=str(fake_claude),
    )
    result = await runner.run(_make_stage(), tmp_path, stage_run_dir)

    assert result.succeeded is True


@pytest.mark.asyncio
async def test_session_error_maps_to_stage_result_failed(tmp_path: Path) -> None:
    """A fixture with is_error=true → StageResult.succeeded=False."""
    fake_claude = _write_fake_claude(tmp_path, "session_error.jsonl")
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    runner = RealStageRunner(
        project_root=project_root,
        claude_binary=str(fake_claude),
    )
    result = await runner.run(_make_stage(), tmp_path, stage_run_dir)

    assert result.succeeded is False


@pytest.mark.asyncio
async def test_nonzero_exit_code_maps_to_failed(tmp_path: Path) -> None:
    """Non-zero subprocess exit code → StageResult.succeeded=False."""
    fake_claude = _write_fake_claude_exitcode(tmp_path, "simple_success.jsonl", 1)
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    runner = RealStageRunner(
        project_root=project_root,
        claude_binary=str(fake_claude),
    )
    result = await runner.run(_make_stage(), tmp_path, stage_run_dir)

    assert result.succeeded is False


@pytest.mark.asyncio
async def test_missing_result_event_maps_to_failed(tmp_path: Path) -> None:
    """Exit-0 stream with no result event → StageResult.succeeded=False."""
    fake_claude = _write_fake_claude(tmp_path, "no_result_event.jsonl")
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    runner = RealStageRunner(
        project_root=project_root,
        claude_binary=str(fake_claude),
    )
    result = await runner.run(_make_stage(), tmp_path, stage_run_dir)

    assert result.succeeded is False


@pytest.mark.asyncio
async def test_cost_extracted_from_result_json(tmp_path: Path) -> None:
    """total_cost_usd from result.json is reflected in StageResult.cost_usd."""
    fake_claude = _write_fake_claude(tmp_path, "simple_success.jsonl")
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    runner = RealStageRunner(
        project_root=project_root,
        claude_binary=str(fake_claude),
    )
    result = await runner.run(_make_stage(), tmp_path, stage_run_dir)

    assert result.cost_usd == pytest.approx(0.00042)


@pytest.mark.asyncio
async def test_messages_jsonl_written_after_run(tmp_path: Path) -> None:
    """After run(), messages.jsonl exists in stage_run_dir/agent0/."""
    fake_claude = _write_fake_claude(tmp_path, "with_one_tool.jsonl")
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(_make_stage(), tmp_path, stage_run_dir)

    assert (stage_run_dir / "agent0" / "messages.jsonl").exists()
    assert (stage_run_dir / "agent0" / "tool-uses.jsonl").exists()
    assert (stage_run_dir / "agent0" / "result.json").exists()


@pytest.mark.asyncio
async def test_subagent_demuxed_during_run(tmp_path: Path) -> None:
    """Subagent messages are demuxed to subagents/ dir during run()."""
    fake_claude = _write_fake_claude(tmp_path, "with_subagent.jsonl")
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(_make_stage(), tmp_path, stage_run_dir)

    sub_dir = stage_run_dir / "agent0" / "subagents" / "toolu_task01"
    assert sub_dir.is_dir()


# ---------------------------------------------------------------------------
# Stop hook integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_hook_called_on_run(tmp_path: Path) -> None:
    """When stop_hook_path is set, a settings.json is generated with a Stop hook."""
    fake_claude = _write_fake_claude(tmp_path, "simple_success.jsonl")
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    # Create a mock hook that records it was called
    mock_hook = tmp_path / "mock_hook.sh"
    called_file = tmp_path / "hook_called"
    mock_hook.write_text(f"#!/usr/bin/env bash\ntouch {called_file}\nexit 0\n")
    mock_hook.chmod(mock_hook.stat().st_mode | stat.S_IEXEC)

    runner = RealStageRunner(
        project_root=project_root,
        claude_binary=str(fake_claude),
        stop_hook_path=mock_hook,
    )
    await runner.run(_make_stage(), tmp_path, stage_run_dir)

    settings_path = stage_run_dir / "session-settings.json"
    assert settings_path.exists(), "session-settings.json should be written"
    settings = json.loads(settings_path.read_text())
    assert "hooks" in settings
    assert "Stop" in settings["hooks"]


@pytest.mark.asyncio
async def test_session_env_vars_set(tmp_path: Path) -> None:
    """HAMMOCK_JOB_DIR and HAMMOCK_STAGE_ID are passed to the subprocess."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    # Use a fake claude that dumps its env to a file, then prints the fixture
    env_dump = tmp_path / "env_dump.txt"
    fixture_path = FIXTURES / "simple_success.jsonl"
    fake_claude = tmp_path / "fake_env_claude"
    fake_claude.write_text(f"#!/usr/bin/env bash\nenv > {env_dump}\ncat {fixture_path}\n")
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    stage_def = _make_stage("my-stage")
    await runner.run(stage_def, tmp_path, stage_run_dir)

    env_text = env_dump.read_text()
    assert "HAMMOCK_JOB_DIR=" in env_text
    assert "HAMMOCK_STAGE_ID=my-stage" in env_text


# ---------------------------------------------------------------------------
# claude CLI flag plumbing — surfaced by post-PR-#21 dogfood
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_invoked_with_verbose_flag(tmp_path: Path) -> None:
    """`claude -p ... --output-format stream-json` requires `--verbose`.

    Without it, claude (>=2.x) exits immediately with:
      Error: When using --print, --output-format=stream-json requires --verbose
    leaving stream.jsonl empty and the stage silently FAILED.
    """
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    args_dump = tmp_path / "argv.txt"
    fixture_path = FIXTURES / "simple_success.jsonl"
    fake_claude = tmp_path / "fake_argv_claude"
    fake_claude.write_text(
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > {args_dump}\ncat {fixture_path}\n'
    )
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(_make_stage("my-stage"), tmp_path, stage_run_dir)

    args = args_dump.read_text().splitlines()
    assert "--verbose" in args, f"--verbose missing from claude argv: {args}"
    assert "--output-format" in args
    assert "stream-json" in args
    assert "-p" in args


@pytest.mark.asyncio
async def test_stderr_captured_to_log_file(tmp_path: Path) -> None:
    """Claude failures (auth, bad flags, segfaults) must be debuggable from
    the job dir alone — capture stderr to ``agent0/stderr.log`` instead of
    discarding it to /dev/null."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run-1"
    stage_run_dir.mkdir()

    fixture_path = FIXTURES / "simple_success.jsonl"
    fake_claude = tmp_path / "fake_stderr_claude"
    # Fake claude prints a recognizable error to stderr, then the fixture
    # to stdout (so the run still "succeeds" from the runner's view).
    fake_claude.write_text(
        f'#!/usr/bin/env bash\necho "DIAGNOSTIC: simulated claude warning" >&2\n'
        f"cat {fixture_path}\n"
    )
    fake_claude.chmod(fake_claude.stat().st_mode | stat.S_IEXEC)

    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(_make_stage("my-stage"), tmp_path, stage_run_dir)

    stderr_log = stage_run_dir / "agent0" / "stderr.log"
    assert stderr_log.exists(), "agent0/stderr.log was not created"
    assert "DIAGNOSTIC: simulated claude warning" in stderr_log.read_text()
