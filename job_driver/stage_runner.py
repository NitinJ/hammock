"""Stage runner protocol + FakeStageRunner.

Per design doc § Stage as universal primitive and implementation.md § Stage 4.

``StageRunner`` is the Protocol; ``FakeStageRunner`` is the test double that
reads from YAML fixture scripts. ``RealStageRunner`` (Stage 5) swaps in the
actual ``claude`` subprocess.

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
    """Async callable that executes one stage."""

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
