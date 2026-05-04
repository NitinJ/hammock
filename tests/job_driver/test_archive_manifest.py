"""Tests for the run-archive integrity manifest.

Per `docs/v0-alignment-report.md` Plan #5: every stage run dir must land
a `manifest.json` with sha256 digests of the archived agent0 files
(stream.jsonl, messages.jsonl, tool-uses.jsonl, result.json, stderr.log,
and any subagent files), so replay can detect bit-rot. Currently no
integrity hashes are computed — design summary § "Run Archive" promised
*"historical record per run, with integrity hashes for replay"*.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from job_driver.archive import ArchiveManifest, compute_manifest, write_manifest
from job_driver.runner import JobDriver
from job_driver.stage_runner import FakeStageRunner
from shared import paths
from shared.atomic import atomic_write_json
from shared.models.job import JobConfig, JobState
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
)

# ---------------------------------------------------------------------------
# Helper unit tests
# ---------------------------------------------------------------------------


def test_compute_manifest_hashes_every_file_under_agent0(tmp_path: Path) -> None:
    """SHA-256 digest per file, keyed by path relative to stage_run_dir."""
    agent0 = tmp_path / "agent0"
    agent0.mkdir()
    (agent0 / "stream.jsonl").write_text('{"x": 1}\n')
    (agent0 / "result.json").write_text('{"ok": true}')
    sub = agent0 / "subagents" / "sa-1"
    sub.mkdir(parents=True)
    (sub / "messages.jsonl").write_text("hello\n")

    manifest = compute_manifest(tmp_path)

    expected_keys = {
        "agent0/stream.jsonl",
        "agent0/result.json",
        "agent0/subagents/sa-1/messages.jsonl",
    }
    assert set(manifest.files.keys()) == expected_keys
    assert manifest.files["agent0/stream.jsonl"] == hashlib.sha256(b'{"x": 1}\n').hexdigest()
    assert manifest.files["agent0/result.json"] == hashlib.sha256(b'{"ok": true}').hexdigest()


def test_compute_manifest_handles_empty_agent0(tmp_path: Path) -> None:
    """A stage run with no agent0/ dir (e.g. fake-runner) yields an empty
    manifest — but the manifest object is still produced so callers can
    write the file unconditionally."""
    (tmp_path / "agent0").mkdir()  # exists but empty
    manifest = compute_manifest(tmp_path)
    assert manifest.files == {}
    assert manifest.algorithm == "sha256"


def test_compute_manifest_skips_when_agent0_absent(tmp_path: Path) -> None:
    manifest = compute_manifest(tmp_path)
    assert manifest.files == {}


def test_write_manifest_atomic_and_round_trips(tmp_path: Path) -> None:
    """write_manifest writes JSON the model can re-load."""
    agent0 = tmp_path / "agent0"
    agent0.mkdir()
    (agent0 / "stream.jsonl").write_text("x\n")
    out = write_manifest(tmp_path)
    assert out == tmp_path / "manifest.json"
    loaded = ArchiveManifest.model_validate_json(out.read_text())
    assert loaded.algorithm == "sha256"
    assert "agent0/stream.jsonl" in loaded.files


# ---------------------------------------------------------------------------
# JobDriver integration test — manifest written on stage close
# ---------------------------------------------------------------------------


def _make_stage(stage_id: str, output: str) -> StageDefinition:
    return StageDefinition(
        id=stage_id,
        worker="agent",
        agent_ref="x",
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=[output]),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(required_outputs=[RequiredOutput(path=output)]),
    )


def _seed_job(tmp_path: Path) -> Path:
    job_dir = tmp_path / "jobs" / "j-mfst"
    job_dir.mkdir(parents=True)
    cfg = JobConfig(
        job_id="jid-mfst",
        job_slug="j-mfst",
        project_slug="p",
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="test",
        state=JobState.SUBMITTED,
    )
    atomic_write_json(job_dir / "job.json", cfg)
    (job_dir / "stage-list.yaml").write_text(
        yaml.dump({"stages": [json.loads(_make_stage("s", "o.txt").model_dump_json())]})
    )
    return job_dir


async def test_stage_close_writes_manifest(tmp_path: Path) -> None:
    """After JobDriver runs a fake stage to SUCCEEDED, the stage_run_dir
    contains manifest.json — even when agent0/ is empty (fake runs
    don't populate it). The file's mere presence is the contract: replay
    tooling can rely on the manifest existing for every closed stage."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "s.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "artifacts": {"o.txt": "."}})
    )
    _seed_job(tmp_path)

    driver = JobDriver(
        "j-mfst",
        root=tmp_path,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    manifest_path = paths.stage_run_dir("j-mfst", "s", 1, root=tmp_path) / "manifest.json"
    assert manifest_path.exists(), f"manifest.json missing at {manifest_path}"
    loaded = ArchiveManifest.model_validate_json(manifest_path.read_text())
    assert loaded.algorithm == "sha256"


async def test_failed_stage_also_writes_manifest(tmp_path: Path) -> None:
    """Manifest written on FAILED too — forensic value is even higher then."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "s.yaml").write_text(yaml.dump({"outcome": "failed", "reason": "boom"}))
    _seed_job(tmp_path)

    driver = JobDriver(
        "j-mfst",
        root=tmp_path,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    manifest_path = paths.stage_run_dir("j-mfst", "s", 1, root=tmp_path) / "manifest.json"
    assert manifest_path.exists()


async def test_runner_exception_path_writes_manifest(tmp_path: Path) -> None:
    """Codex review MEDIUM 2 — when the runner raises (not a clean
    `outcome: failed`, but an actual exception), JobDriver routes
    through `_fail_stage` which previously skipped the manifest. After
    centralising the manifest write, this path lands one too."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _seed_job(tmp_path)

    class _ExplodingRunner:
        async def run(self, stage_def, job_dir, stage_run_dir):
            raise RuntimeError("simulated runner crash")

    driver = JobDriver(
        "j-mfst",
        root=tmp_path,
        stage_runner=_ExplodingRunner(),  # type: ignore[arg-type]
        heartbeat_interval=0.1,
    )
    await driver.run()

    # _run_single_stage created run-1 and the runner raised inside it.
    manifest_path = paths.stage_run_dir("j-mfst", "s", 1, root=tmp_path) / "manifest.json"
    assert manifest_path.exists(), "exception-routed _fail_stage must still write the manifest"


async def test_wall_clock_overrun_writes_manifest(tmp_path: Path) -> None:
    """Codex review LOW 3 — wall-clock-overrun path also writes the manifest.

    The fake stage sleeps past its (sub-minute) wall-clock cap; the
    JobDriver's watchdog wins and writes manifest before returning.
    """
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "s.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "delay_seconds": 5.0, "artifacts": {"o.txt": "."}})
    )
    job_dir = tmp_path / "jobs" / "j-mfst"
    job_dir.mkdir(parents=True)
    cfg = JobConfig(
        job_id="jid",
        job_slug="j-mfst",
        project_slug="p",
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="t",
        state=JobState.SUBMITTED,
    )
    atomic_write_json(job_dir / "job.json", cfg)
    # Stage with a sub-minute cap (0.02 min ≈ 1.2 s).
    capped_stage = StageDefinition(
        id="s",
        worker="agent",
        agent_ref="x",
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=["o.txt"]),
        budget=Budget(max_wall_clock_min=0.02),
        exit_condition=ExitCondition(required_outputs=[RequiredOutput(path="o.txt")]),
    )
    (job_dir / "stage-list.yaml").write_text(
        yaml.dump({"stages": [json.loads(capped_stage.model_dump_json())]})
    )

    driver = JobDriver(
        "j-mfst",
        root=tmp_path,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    manifest_path = paths.stage_run_dir("j-mfst", "s", 1, root=tmp_path) / "manifest.json"
    assert manifest_path.exists(), "wall-clock-overrun branch must write the manifest"


async def test_budget_overrun_post_check_writes_manifest(tmp_path: Path) -> None:
    """Codex review LOW 3 — budget-overrun post-check path also writes
    the manifest (not just the wall-clock branch)."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    (fixtures / "s.yaml").write_text(
        yaml.dump({"outcome": "succeeded", "cost_usd": 5.0, "artifacts": {"o.txt": "."}})
    )
    job_dir = tmp_path / "jobs" / "j-mfst"
    job_dir.mkdir(parents=True)
    cfg = JobConfig(
        job_id="jid",
        job_slug="j-mfst",
        project_slug="p",
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="t",
        state=JobState.SUBMITTED,
    )
    atomic_write_json(job_dir / "job.json", cfg)
    capped_stage = StageDefinition(
        id="s",
        worker="agent",
        agent_ref="x",
        inputs=InputSpec(required=[], optional=None),
        outputs=OutputSpec(required=["o.txt"]),
        budget=Budget(max_budget_usd=1.0),
        exit_condition=ExitCondition(required_outputs=[RequiredOutput(path="o.txt")]),
    )
    (job_dir / "stage-list.yaml").write_text(
        yaml.dump({"stages": [json.loads(capped_stage.model_dump_json())]})
    )

    driver = JobDriver(
        "j-mfst",
        root=tmp_path,
        stage_runner=FakeStageRunner(fixtures),
        heartbeat_interval=0.1,
    )
    await driver.run()

    manifest_path = paths.stage_run_dir("j-mfst", "s", 1, root=tmp_path) / "manifest.json"
    assert manifest_path.exists(), "budget-overrun post-check branch must write the manifest"
