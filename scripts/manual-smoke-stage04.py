"""Stage 4 manual smoke.

Spawns a real Job Driver subprocess against a compiled job dir, using
``FakeStageRunner`` fixtures to simulate stage execution. Verifies the job
transitions through SUBMITTED → STAGES_RUNNING → COMPLETED and that stage
state files + events are written.

Run with::

    uv run python scripts/manual-smoke-stage04.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from shared import paths  # noqa: E402
from shared.atomic import atomic_write_json  # noqa: E402
from shared.models.job import JobConfig, JobState  # noqa: E402
from shared.models.stage import (  # noqa: E402
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
    StageRun,
    StageState,
)

_FIXTURES = {
    "write-spec.yaml": "outcome: succeeded\nartifacts:\n  spec.md: '# Spec'\n",
    "review-spec.yaml": 'outcome: succeeded\nartifacts:\n  review.json: \'{"verdict": "approved"}\'\n',
}


def _make_stage(
    stage_id: str, required_inputs: list[str], required_outputs: list[str]
) -> StageDefinition:
    return StageDefinition(
        id=stage_id,
        worker="agent",
        inputs=InputSpec(required=required_inputs, optional=None),
        outputs=OutputSpec(required=required_outputs),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(
            required_outputs=[RequiredOutput(path=p) for p in required_outputs] or None
        ),
    )


def _ok(label: str) -> None:
    print(f"  ✓ {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"  ✗ {label}" + (f": {detail}" if detail else ""))
    raise SystemExit(1)


async def _run_driver(job_slug: str, root: Path, fixtures_dir: Path) -> None:
    import subprocess

    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "python",
            "-m",
            "job_driver",
            job_slug,
            "--root",
            str(root),
            "--fake-fixtures",
            str(fixtures_dir),
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for job to reach terminal state (max 30s)
    deadline = time.time() + 30
    while time.time() < deadline:
        await asyncio.sleep(0.2)
        job_json_path = paths.job_json(job_slug, root=root)
        if job_json_path.exists():
            cfg = JobConfig.model_validate_json(job_json_path.read_text())
            if cfg.state in (JobState.COMPLETED, JobState.FAILED, JobState.ABANDONED):
                proc.wait(timeout=5)
                return
    proc.kill()
    proc.wait()
    _fail("driver did not reach terminal state within 30s")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hammock-smoke04-") as tmp:
        root = Path(tmp) / "root"
        root.mkdir()
        fixtures_dir = Path(tmp) / "fixtures"
        fixtures_dir.mkdir()
        job_slug = "smoke-04-job"

        print(f"smoke root: {root}")

        # Write fixtures
        for name, content in _FIXTURES.items():
            (fixtures_dir / name).write_text(content)

        # Write job dir
        job_dir = paths.job_dir(job_slug, root=root)
        job_dir.mkdir(parents=True)
        job_config = JobConfig(
            job_id="smoke-04-id",
            job_slug=job_slug,
            project_slug="smoke-project",
            job_type="build-feature",
            created_by="human",
            state=JobState.SUBMITTED,
            created_at="2026-01-01T00:00:00+00:00",  # type: ignore[arg-type]
        )
        atomic_write_json(paths.job_json(job_slug, root=root), job_config)

        stages = [
            _make_stage("write-spec", [], ["spec.md"]),
            _make_stage("review-spec", ["spec.md"], ["review.json"]),
        ]
        stage_list_data = {"stages": [json.loads(s.model_dump_json()) for s in stages]}
        paths.job_stage_list(job_slug, root=root).write_text(yaml.dump(stage_list_data))

        # Run the driver
        asyncio.run(_run_driver(job_slug, root, fixtures_dir))

        # --- Assertions ---
        cfg = JobConfig.model_validate_json(paths.job_json(job_slug, root=root).read_text())
        if cfg.state != JobState.COMPLETED:
            _fail("job.state", f"expected COMPLETED, got {cfg.state}")
        _ok("job reached COMPLETED")

        # Stage state files
        for stage_id in ("write-spec", "review-spec"):
            sj = paths.stage_json(job_slug, stage_id, root=root)
            if not sj.exists():
                _fail(f"{stage_id}/stage.json missing")
            sr = StageRun.model_validate_json(sj.read_text())
            if sr.state != StageState.SUCCEEDED:
                _fail(f"{stage_id} state", f"expected SUCCEEDED, got {sr.state}")
            _ok(f"{stage_id} → SUCCEEDED")

        # Outputs written
        if not (job_dir / "spec.md").exists():
            _fail("spec.md not written by FakeStageRunner")
        _ok("spec.md written")
        review_data = json.loads((job_dir / "review.json").read_text())
        assert review_data["verdict"] == "approved"
        _ok("review.json written with correct content")

        # Heartbeat
        hb = paths.job_heartbeat(job_slug, root=root)
        if not hb.exists():
            _fail("heartbeat file missing")
        _ok("heartbeat file exists")

        # Events
        ev_path = paths.job_events_jsonl(job_slug, root=root)
        if not ev_path.exists():
            _fail("events.jsonl missing")
        events = [json.loads(line) for line in ev_path.read_text().splitlines() if line.strip()]
        event_types = {e["event_type"] for e in events}
        for expected in ("job_state_transition", "stage_state_transition"):
            if expected not in event_types:
                _fail(f"no {expected} events found")
        _ok(f"events.jsonl has {len(events)} events including job+stage transitions")

        print("\nsmoke OK: job driver completed 2-stage job end-to-end")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
