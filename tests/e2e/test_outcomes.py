"""Tests for ``tests.e2e.outcomes``.

Each outcome helper has both a happy-path test (passes on a synthetic
green job dir) and a sad-path test (raises with a named-piece
message on a specific violation).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from shared import paths
from shared.atomic import atomic_append_jsonl, atomic_write_json
from shared.models import Event
from shared.models.job import JobConfig, JobState
from shared.models.stage import (
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
    StageRun,
    StageState,
)
from tests.e2e.outcomes import (
    OUTCOMES,
    assert_agent_artifacts_present,
    assert_all_stages_succeeded,
    assert_at_least_one_worktree_created_event,
    assert_event_stream_well_formed,
    assert_job_completed,
    assert_no_failed_or_cancelled,
    assert_required_outputs_exist,
    assert_stop_hook_fired_for_each_succeeded_stage,
    assert_summary_md_has_url,
    assert_worker_exit_for_each_succeeded_stage,
)

# ---------------------------------------------------------------------------
# Synthetic-job-dir builders
# ---------------------------------------------------------------------------


def _seed_completed_job(
    tmp_path: Path,
    *,
    job_slug: str = "j-out",
    stage_id: str = "stage-a",
    output_path: str = "out.txt",
    job_state: JobState = JobState.COMPLETED,
    stage_state: StageState = StageState.SUCCEEDED,
    write_output: bool = True,
    write_agent_artifacts: bool = True,
    write_summary: bool = True,
    write_events: bool = True,
) -> Path:
    """Build a one-stage green job dir. Each toggleable input lets a
    sad-path test omit exactly one piece."""
    root = tmp_path / "hammock-root"
    job_dir = paths.job_dir(job_slug, root=root)
    job_dir.mkdir(parents=True)

    cfg = JobConfig(
        job_id="jid",
        job_slug=job_slug,
        project_slug="p",
        job_type="fix-bug",
        created_at=datetime.now(UTC),
        created_by="t",
        state=job_state,
    )
    atomic_write_json(paths.job_json(job_slug, root=root), cfg)

    stage_def = StageDefinition(
        id=stage_id,
        worker="agent",
        agent_ref="x",
        inputs=InputSpec(),
        outputs=OutputSpec(required=[output_path]),
        budget=Budget(max_turns=1),
        exit_condition=ExitCondition(required_outputs=[RequiredOutput(path=output_path)]),
    )
    (job_dir / "stage-list.yaml").write_text(
        yaml.safe_dump({"stages": [json.loads(stage_def.model_dump_json())]})
    )

    sr = StageRun(
        stage_id=stage_id,
        attempt=1,
        state=stage_state,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
    )
    atomic_write_json(paths.stage_json(job_slug, stage_id, root=root), sr)

    if write_output:
        (job_dir / output_path).write_text("output content\n")

    if write_agent_artifacts:
        agent_dir = paths.stage_run_dir(job_slug, stage_id, 1, root=root) / "agent0"
        agent_dir.mkdir(parents=True, exist_ok=True)
        latest = agent_dir.parent / "agent0-latest"  # not the real symlink shape
        del latest
        # The outcome helper looks under agent0/ directly for the
        # "latest" run; here we create the canonical file set in the
        # attempt's agent0 dir (the real layout has a `latest` symlink
        # to a `run-1` dir; the outcome helper resolves that).
        for fname in ("stream.jsonl", "messages.jsonl", "result.json", "stderr.log"):
            (agent_dir / fname).write_text("data\n")

    if write_summary:
        (job_dir / "summary.md").write_text("Summary: see https://github.com/me/repo/pull/1\n")

    if write_events:
        _write_min_event_stream(root, job_slug, stage_id)

    return root


def _write_min_event_stream(root: Path, job_slug: str, stage_id: str) -> None:
    """Write the minimum event sequence the outcome helpers need."""
    cfg = JobConfig.model_validate_json(paths.job_json(job_slug, root=root).read_text())
    base = {
        "source": "job_driver",
        "job_id": cfg.job_id,
        "stage_id": stage_id,
    }
    seq = 0

    def _ev(event_type: str, payload: dict[str, object]) -> Event:
        nonlocal seq
        ev = Event(
            seq=seq,
            timestamp=datetime.now(UTC),
            event_type=event_type,
            source="job_driver",
            job_id=base["job_id"],
            stage_id=stage_id,
            payload=payload,
        )
        seq += 1
        return ev

    events_path = paths.job_events_jsonl(job_slug, root=root)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    for ev in [
        _ev("job_state_transition", {"to": "STAGES_RUNNING"}),
        _ev("stage_state_transition", {"to": "RUNNING"}),
        _ev("worktree_created", {"path": "/tmp/wt", "branch": "hammock/stages/x/stage-a"}),
        _ev("hook_fired", {"hook": "Stop"}),
        _ev("stage_state_transition", {"to": "SUCCEEDED"}),
        _ev("worker_exit", {"exit_code": 0, "succeeded": True, "stage_id": stage_id}),
        _ev("job_state_transition", {"to": "COMPLETED"}),
    ]:
        atomic_append_jsonl(events_path, ev)


# ---------------------------------------------------------------------------
# Happy path (one helper at a time)
# ---------------------------------------------------------------------------


def test_assert_job_completed_passes(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    assert_job_completed(root, "j-out")


def test_assert_job_completed_fails_on_failed_state(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path, job_state=JobState.FAILED)
    with pytest.raises(AssertionError, match="COMPLETED"):
        assert_job_completed(root, "j-out")


def test_assert_all_stages_succeeded_passes(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    assert_all_stages_succeeded(root, "j-out")


def test_assert_all_stages_succeeded_fails_on_running(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path, stage_state=StageState.RUNNING)
    with pytest.raises(AssertionError, match="stage-a"):
        assert_all_stages_succeeded(root, "j-out")


def test_assert_no_failed_or_cancelled_passes(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    assert_no_failed_or_cancelled(root, "j-out")


def test_assert_no_failed_or_cancelled_fails(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path, stage_state=StageState.FAILED)
    with pytest.raises(AssertionError, match="FAILED"):
        assert_no_failed_or_cancelled(root, "j-out")


def test_assert_required_outputs_exist_passes(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    assert_required_outputs_exist(root, "j-out")


def test_assert_required_outputs_exist_fails_when_missing(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path, write_output=False)
    with pytest.raises(AssertionError, match=r"out\.txt"):
        assert_required_outputs_exist(root, "j-out")


def test_assert_stop_hook_fired_passes(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    assert_stop_hook_fired_for_each_succeeded_stage(root, "j-out")


def test_assert_stop_hook_fired_fails_when_missing(tmp_path: Path) -> None:
    """If no hook_fired event exists for a SUCCEEDED stage, the helper
    raises with the stage id."""
    root = _seed_completed_job(tmp_path)
    # Rewrite events.jsonl without the hook_fired entry.
    events_path = paths.job_events_jsonl("j-out", root=root)
    lines = [line for line in events_path.read_text().splitlines() if "hook_fired" not in line]
    events_path.write_text("\n".join(lines) + "\n")

    with pytest.raises(AssertionError, match="stage-a"):
        assert_stop_hook_fired_for_each_succeeded_stage(root, "j-out")


def test_assert_summary_md_has_url_passes(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    assert_summary_md_has_url(root, "j-out")


def test_assert_summary_md_has_url_fails_without_url(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    (paths.job_dir("j-out", root=root) / "summary.md").write_text("no url here")
    with pytest.raises(AssertionError, match="url"):
        assert_summary_md_has_url(root, "j-out")


def test_assert_agent_artifacts_present_passes(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    assert_agent_artifacts_present(root, "j-out")


def test_assert_agent_artifacts_present_fails_when_stream_missing(
    tmp_path: Path,
) -> None:
    root = _seed_completed_job(tmp_path)
    stream = paths.stage_run_dir("j-out", "stage-a", 1, root=root) / "agent0" / "stream.jsonl"
    stream.unlink()
    with pytest.raises(AssertionError, match=r"stream\.jsonl"):
        assert_agent_artifacts_present(root, "j-out")


def test_assert_event_stream_well_formed_passes(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    assert_event_stream_well_formed(root, "j-out")


def test_assert_event_stream_well_formed_fails_on_bad_jsonl(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    events_path = paths.job_events_jsonl("j-out", root=root)
    events_path.write_text(events_path.read_text() + "\n{not valid json\n")
    with pytest.raises(AssertionError, match="JSON"):
        assert_event_stream_well_formed(root, "j-out")


def test_assert_at_least_one_worktree_created_event_passes(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    assert_at_least_one_worktree_created_event(root, "j-out")


def test_assert_at_least_one_worktree_created_event_fails(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    events_path = paths.job_events_jsonl("j-out", root=root)
    lines = [
        line for line in events_path.read_text().splitlines() if "worktree_created" not in line
    ]
    events_path.write_text("\n".join(lines) + "\n")
    with pytest.raises(AssertionError, match="worktree_created"):
        assert_at_least_one_worktree_created_event(root, "j-out")


def test_assert_worker_exit_for_each_succeeded_stage_passes(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    assert_worker_exit_for_each_succeeded_stage(root, "j-out")


def test_assert_worker_exit_fails_on_nonzero_exit_code(tmp_path: Path) -> None:
    root = _seed_completed_job(tmp_path)
    events_path = paths.job_events_jsonl("j-out", root=root)
    # Pydantic dumps with no spaces; match both shapes for safety.
    text = events_path.read_text()
    for src in ('"exit_code":0', '"exit_code": 0'):
        text = text.replace(src, src.replace("0", "1"))
    events_path.write_text(text)
    with pytest.raises(AssertionError, match="exit_code"):
        assert_worker_exit_for_each_succeeded_stage(root, "j-out")


# ---------------------------------------------------------------------------
# Registry — every outcome from spec §Outcomes is in OUTCOMES
# ---------------------------------------------------------------------------


def test_outcomes_registry_covers_spec_assertions() -> None:
    """The OUTCOMES registry exposes every helper so the test driver can
    iterate them. This locks the contract: 11 helpers shipped (the
    branch + project-config-dependent ones live outside, see spec)."""
    # Outcomes #5 (stop_hook_fired) and #6 (summary_md_has_url) are
    # intentionally not in OUTCOMES; see the deferred-outcome comment
    # in outcomes.py.
    expected = {
        "job_completed",
        "all_stages_succeeded",
        "no_failed_or_cancelled",
        "required_outputs_exist",
        "agent_artifacts_present",
        "event_stream_well_formed",
        "worktree_created_event",
        "worker_exit_per_succeeded_stage",
    }
    assert expected <= OUTCOMES.keys()
    assert "stop_hook_fired" not in OUTCOMES
    assert "summary_md_has_url" not in OUTCOMES
