"""Stage runner protocol, FakeStageRunner, and RealStageRunner.

Per design doc § Stage as universal primitive, § Observability, and
implementation.md §§ Stage 4-5.

``StageRunner`` is the Protocol; ``FakeStageRunner`` is the test double that
reads from YAML fixture scripts; ``RealStageRunner`` (Stage 5) spawns the
actual ``claude`` subprocess and extracts stream-json output.

Fixture format (``tests/fixtures/fake-runs/<stage_id>.yaml``):

.. code-block:: yaml

    outcome: succeeded      # or: failed
    delay_seconds: 0.0      # simulated work duration (default 0)
    cost_usd: 0.05
    artifacts:
      problem-spec.md: |
        # Problem Specification
        Test content here.
    reason: null            # failure reason string (used when outcome=failed)

If no fixture file exists the runner succeeds with no outputs (safe default for
stages that produce no artifacts in tests).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import yaml

if TYPE_CHECKING:
    from shared.models.stage import StageDefinition


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    """Outcome of a single stage run attempt."""

    succeeded: bool
    reason: str | None = None
    outputs_produced: list[str] = field(default_factory=list)
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class StageRunner(Protocol):
    """Async callable that executes one stage.

    Signature is ``run(stage_def, job_dir, stage_run_dir) -> StageResult``.

    The implementation.md § Stage 4 task DAG sketches this as
    ``run(stage_def, work_dir)``; we split ``work_dir`` into two explicit
    args because runners need both the **job-level** dir (for shared
    artifacts like ``spec.md`` that span stages) and the **stage-run**
    dir (for per-attempt logs / nudges / pr-info). This is the contract
    Stage 5's ``RealStageRunner`` will bind to.
    """

    async def run(
        self,
        stage_def: StageDefinition,
        job_dir: Path,
        stage_run_dir: Path,
    ) -> StageResult: ...


# ---------------------------------------------------------------------------
# FakeStageRunner
# ---------------------------------------------------------------------------


class FakeStageRunner:
    """Simulates stage execution from YAML fixture scripts.

    Fixture lookup order:
    1. ``<fixtures_dir>/<stage_id>.yaml``
    2. Missing fixture → succeed with no outputs (safe default).
    """

    def __init__(self, fixtures_dir: Path) -> None:
        self._fixtures_dir = fixtures_dir

    async def run(
        self,
        stage_def: StageDefinition,
        job_dir: Path,
        stage_run_dir: Path,
    ) -> StageResult:
        fixture_path = self._fixtures_dir / f"{stage_def.id}.yaml"

        if not fixture_path.exists():
            return StageResult(succeeded=True)

        spec = yaml.safe_load(fixture_path.read_text()) or {}
        delay = float(spec.get("delay_seconds", 0.0))
        outcome = spec.get("outcome", "succeeded")
        reason: str | None = spec.get("reason")
        cost_usd = float(spec.get("cost_usd", 0.0))
        artifacts: dict[str, str] = spec.get("artifacts") or {}

        if delay > 0:
            await asyncio.sleep(delay)

        outputs_produced: list[str] = []
        if outcome == "succeeded":
            for filename, content in artifacts.items():
                dest = job_dir / filename
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(str(content))
                outputs_produced.append(filename)

        return StageResult(
            succeeded=(outcome == "succeeded"),
            reason=reason,
            outputs_produced=outputs_produced,
            cost_usd=cost_usd,
        )


# ---------------------------------------------------------------------------
# RealStageRunner
# ---------------------------------------------------------------------------


class RealStageRunner:
    """Spawns a real ``claude`` subprocess per stage.

    Per design doc § Observability — runs
    ``claude -p <prompt> --output-format stream-json --settings <path>``,
    captures stdout line-by-line to ``stream.jsonl``, then calls
    ``StreamExtractor.extract()`` to derive ``messages.jsonl``,
    ``tool-uses.jsonl``, ``result.json``, and per-subagent dirs.

    ``stop_hook_path`` — if set, a Stop hook entry pointing to the script is
    written into the per-session settings file; the hook validates required
    outputs and blocks session exit on failures.  Stage 6 replaces the
    generated settings with full specialist resolution.

    ``--channels dashboard`` stub: Stage 6 wires up the real MCP server;
    Stage 5 omits the flag (no server is running yet).
    """

    def __init__(
        self,
        *,
        project_root: Path,
        claude_binary: str = "claude",
        stop_hook_path: Path | None = None,
    ) -> None:
        self._project_root = project_root
        self._claude_binary = claude_binary
        self._stop_hook_path = stop_hook_path

    async def run(
        self,
        stage_def: StageDefinition,
        job_dir: Path,
        stage_run_dir: Path,
    ) -> StageResult:
        from job_driver.stream_extractor import StreamExtractor

        agent0_dir = stage_run_dir / "agent0"
        agent0_dir.mkdir(parents=True, exist_ok=True)

        # Write per-session settings (Stop hook wiring)
        settings_path = stage_run_dir / "session-settings.json"
        self._write_session_settings(settings_path, stage_def)

        # Build command: use stage description as the agent's initial prompt
        prompt = stage_def.description or stage_def.id
        cmd = [
            self._claude_binary,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--settings",
            str(settings_path),
        ]

        # Build subprocess environment with Hammock context for the hook
        env = self._build_env(job_dir, stage_def)

        stream_path = agent0_dir / "stream.jsonl"
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(self._project_root),
            env=env,
        )
        assert proc.stdout is not None

        # Stream stdout to stream.jsonl line-by-line
        fd = os.open(stream_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            async for line in proc.stdout:
                os.write(fd, line)
        finally:
            os.close(fd)

        await proc.wait()
        return_code = proc.returncode or 0

        # Extract stream → messages.jsonl, tool-uses.jsonl, result.json, subagents/
        summary = StreamExtractor.extract(stream_path, agent0_dir)

        # Map result to StageResult
        cost_usd = 0.0
        succeeded = return_code == 0

        if summary.result is not None:
            cost_usd = float(summary.result.get("total_cost_usd") or 0.0)
            if summary.result.get("is_error"):
                succeeded = False

        return StageResult(
            succeeded=succeeded,
            cost_usd=cost_usd,
            outputs_produced=[],  # tracked via MCP task records in Stage 6
        )

    def _write_session_settings(self, settings_path: Path, stage_def: StageDefinition) -> None:
        """Write a per-session settings.json with optional Stop hook."""
        hooks: dict[str, object] = {}
        if self._stop_hook_path is not None:
            hooks["Stop"] = [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"bash {self._stop_hook_path}",
                        }
                    ]
                }
            ]
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps({"hooks": hooks}, indent=2))

    def _build_env(self, job_dir: Path, stage_def: StageDefinition) -> dict[str, str]:
        """Build subprocess env with HAMMOCK_* vars for the Stop hook."""
        env = dict(os.environ)
        env["HAMMOCK_JOB_DIR"] = str(job_dir)
        env["HAMMOCK_STAGE_ID"] = stage_def.id
        if stage_def.exit_condition.required_outputs:
            env["HAMMOCK_STAGE_REQUIRED_OUTPUTS"] = "\n".join(
                ro.path for ro in stage_def.exit_condition.required_outputs
            )
        return env
