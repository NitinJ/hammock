"""Stage 6 wiring: ``RealStageRunner`` integrates with ``MCPManager``.

The runner must spawn an MCP server descriptor before the stage starts
and dispose it after the session exits. The mcp_config must be merged
into the per-session settings so Claude Code launches the MCP server on
demand.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dashboard.mcp.manager import MCPManager, ServerHandle
from job_driver.stage_runner import RealStageRunner
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    StageDefinition,
)


def _stage(stage_id: str = "implement-1") -> StageDefinition:
    return StageDefinition(
        id=stage_id,
        worker="agent",
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=[]),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(required_outputs=None),
    )


def _write_fake_claude(path: Path) -> None:
    """Fake claude that emits a minimal valid result event then exits 0."""
    path.write_text(
        "#!/usr/bin/env bash\n"
        'echo \'{"type":"system","subtype":"init","session_id":"x"}\'\n'
        'echo \'{"type":"result","subtype":"success","is_error":false,'
        '"total_cost_usd":0.0,"session_id":"x"}\'\n'
    )
    os.chmod(path, 0o755)


async def test_real_runner_spawns_and_disposes_mcp(tmp_path: Path, hammock_root: Path) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    fake_claude = tmp_path / "claude"
    _write_fake_claude(fake_claude)

    spawn_calls: list[tuple[str, str]] = []
    dispose_calls: list[ServerHandle] = []

    class _RecordingManager(MCPManager):
        def spawn(
            self,
            *,
            job_slug: str,
            stage_id: str,
            root: Path | None = None,
        ) -> ServerHandle:
            spawn_calls.append((job_slug, stage_id))
            return super().spawn(job_slug=job_slug, stage_id=stage_id, root=root)

        def dispose(self, handle: ServerHandle) -> None:
            dispose_calls.append(handle)
            super().dispose(handle)

    mgr = _RecordingManager()
    runner = RealStageRunner(
        project_root=project_root,
        claude_binary=str(fake_claude),
        mcp_manager=mgr,
        job_slug="proj/feat",
        hammock_root=hammock_root,
    )

    stage_run_dir = tmp_path / "run-1"
    stage_run_dir.mkdir()
    job_dir = tmp_path / "job-dir"
    job_dir.mkdir()

    result = await runner.run(_stage(), job_dir, stage_run_dir)
    assert result.succeeded
    assert spawn_calls == [("proj/feat", "implement-1")]
    assert len(dispose_calls) == 1


async def test_real_runner_writes_mcp_config_into_settings(
    tmp_path: Path, hammock_root: Path
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    fake_claude = tmp_path / "claude"
    _write_fake_claude(fake_claude)

    runner = RealStageRunner(
        project_root=project_root,
        claude_binary=str(fake_claude),
        mcp_manager=MCPManager(),
        job_slug="proj/feat",
        hammock_root=hammock_root,
    )

    stage_run_dir = tmp_path / "run-1"
    stage_run_dir.mkdir()
    job_dir = tmp_path / "job-dir"
    job_dir.mkdir()

    await runner.run(_stage(), job_dir, stage_run_dir)

    settings_path = stage_run_dir / "session-settings.json"
    settings = json.loads(settings_path.read_text())
    assert "mcpServers" in settings
    assert "hammock-dashboard" in settings["mcpServers"]
    server = settings["mcpServers"]["hammock-dashboard"]
    assert "proj/feat" in server["args"]
    assert "implement-1" in server["args"]
