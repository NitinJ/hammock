"""Per `docs/v0-alignment-report.md` Plan #3 — RealStageRunner passes
the materialised agents catalogue to claude via the documented
``--agents <json>`` flag when ``<stage_run_dir>/agents.json`` exists
and is non-empty. This is what makes per-project agent overrides
actually take effect at runtime.
"""

from __future__ import annotations

import json
import stat
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
    args_dump = tmp_path / "argv.txt"
    fixture = FIXTURES / "simple_success.jsonl"
    script = tmp_path / "fake_argv_claude"
    script.write_text(f'#!/usr/bin/env bash\nprintf "%s\\0" "$@" > {args_dump}\ncat {fixture}\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, args_dump


async def test_real_runner_passes_agents_flag_when_agents_json_present(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run"
    stage_run_dir.mkdir()
    agents_payload = {
        "bug-report-writer": {
            "description": "Frames the human prompt as a bug report.",
            "prompt": "You are a bug report writer.",
        },
    }
    (stage_run_dir / "agents.json").write_text(json.dumps(agents_payload))

    fake_claude, args_dump = _argv_capture_claude(tmp_path)
    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(_stage(), tmp_path, stage_run_dir)

    args = args_dump.read_text().split("\0")
    assert "--agents" in args, f"--agents flag missing from claude argv: {args}"
    idx = args.index("--agents")
    inline = args[idx + 1]
    parsed = json.loads(inline)
    assert "bug-report-writer" in parsed
    assert parsed["bug-report-writer"]["description"].startswith("Frames")


async def test_real_runner_skips_agents_flag_when_agents_json_absent(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run"
    stage_run_dir.mkdir()
    # No agents.json on disk.

    fake_claude, args_dump = _argv_capture_claude(tmp_path)
    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(_stage(), tmp_path, stage_run_dir)

    args = args_dump.read_text().split("\0")
    assert "--agents" not in args


async def test_real_runner_skips_agents_flag_when_agents_json_empty(tmp_path: Path) -> None:
    """An empty `{}` agents.json (no overrides) → no --agents flag, so
    claude uses its default agent set rather than getting an empty
    custom list."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    stage_run_dir = tmp_path / "stage-run"
    stage_run_dir.mkdir()
    (stage_run_dir / "agents.json").write_text("{}")

    fake_claude, args_dump = _argv_capture_claude(tmp_path)
    runner = RealStageRunner(project_root=project_root, claude_binary=str(fake_claude))
    await runner.run(_stage(), tmp_path, stage_run_dir)

    args = args_dump.read_text().split("\0")
    assert "--agents" not in args
