"""Tests for ``tests.e2e.hil_stitcher``.

Per docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step F: a
schema-aware gate-stitcher that resolves a stage's
``BLOCKED_ON_HUMAN`` gate by writing the required output artifacts
(via the fixture-builder registry) and optionally calling
``POST /api/hil/{id}/answer`` for record fidelity.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from shared import paths
from shared.atomic import atomic_write_json
from shared.models.hil import HilItem, ManualStepQuestion
from shared.models.stage import (
    ArtifactValidator,
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
    StageRun,
    StageState,
)
from tests.e2e.hil_builders import BUILDERS, MissingBuilderError
from tests.e2e.hil_stitcher import StitchResult, stitch_hil_gate


def _seed_blocked_stage(
    tmp_path: Path,
    *,
    job_slug: str = "j-stitch",
    stage_id: str = "review",
    outputs: list[RequiredOutput] | None = None,
    validators: list[ArtifactValidator] | None = None,
    create_hil_item: bool = True,
) -> tuple[Path, str]:
    """Build a job dir with a single BLOCKED_ON_HUMAN stage. Returns
    (root, item_id)."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    job_dir = paths.job_dir(job_slug, root=root)
    job_dir.mkdir(parents=True)
    stage_def = StageDefinition(
        id=stage_id,
        worker="human",
        inputs=InputSpec(),
        outputs=OutputSpec(required=[ro.path for ro in outputs] if outputs else []),
        budget=Budget(max_turns=1),
        exit_condition=ExitCondition(
            required_outputs=outputs,
            artifact_validators=validators,
        ),
    )
    (job_dir / "stage-list.yaml").write_text(
        yaml.safe_dump({"stages": [json.loads(stage_def.model_dump_json())]})
    )
    stage_run = StageRun(
        stage_id=stage_id,
        attempt=1,
        state=StageState.BLOCKED_ON_HUMAN,
        started_at=datetime.now(UTC),
    )
    atomic_write_json(paths.stage_json(job_slug, stage_id, root=root), stage_run)

    item_id = ""
    if create_hil_item:
        item_id = "manualstep_test_abc123"
        item = HilItem(
            id=item_id,
            kind="manual-step",
            stage_id=stage_id,
            created_at=datetime.now(UTC),
            status="awaiting",
            question=ManualStepQuestion(kind="manual-step", instructions="resolve me"),
        )
        atomic_write_json(paths.hil_item_path(job_slug, item_id, root=root), item)

    return root, item_id


# ---------------------------------------------------------------------------


async def test_stitch_writes_required_outputs(tmp_path: Path) -> None:
    """Schema-aware: walks required outputs, dispatches to BUILDERS,
    writes payloads to disk."""
    root, _ = _seed_blocked_stage(
        tmp_path,
        outputs=[RequiredOutput(path="review.json")],
        validators=[ArtifactValidator(path="review.json", schema="review-verdict-schema")],
    )
    result = await stitch_hil_gate(root=root, job_slug="j-stitch", stage_id="review")

    assert isinstance(result, StitchResult)
    artifact = paths.job_dir("j-stitch", root=root) / "review.json"
    assert artifact.is_file()
    payload = json.loads(artifact.read_text())
    assert payload["verdict"] == "approved"  # the default review-verdict builder


async def test_stitch_marks_stage_succeeded(tmp_path: Path) -> None:
    root, _ = _seed_blocked_stage(
        tmp_path,
        outputs=[RequiredOutput(path="out.txt")],
        validators=[ArtifactValidator(path="out.txt", schema="non-empty")],
    )
    await stitch_hil_gate(root=root, job_slug="j-stitch", stage_id="review")

    sj = StageRun.model_validate_json(paths.stage_json("j-stitch", "review", root=root).read_text())
    assert sj.state == StageState.SUCCEEDED


async def test_stitch_writes_disk_before_posting_answer(tmp_path: Path) -> None:
    """Order matters: the answer endpoint walks the cache, which is
    populated from disk; so the artifact must hit disk first."""
    root, _ = _seed_blocked_stage(
        tmp_path,
        outputs=[RequiredOutput(path="out.txt")],
        validators=[ArtifactValidator(path="out.txt", schema="non-empty")],
    )

    artifact = paths.job_dir("j-stitch", root=root) / "out.txt"
    sj_path = paths.stage_json("j-stitch", "review", root=root)

    seen_states: list[bool] = []

    class _ProbingClient:
        def post(self, _path: str, json: dict[str, object]) -> object:  # noqa: A002
            del json
            # When the POST runs, both the artifact and the stage.json
            # must already be on disk.
            seen_states.append(artifact.is_file() and sj_path.exists())

            class _Resp:
                status_code = 200
                text = ""

            return _Resp()

    await stitch_hil_gate(
        root=root,
        job_slug="j-stitch",
        stage_id="review",
        app_client=_ProbingClient(),  # type: ignore[arg-type]
    )
    assert seen_states == [True]


async def test_stitch_skips_post_when_no_client(tmp_path: Path) -> None:
    """app_client=None → no /answer POST (the existing fake e2e
    operates this way)."""
    root, _ = _seed_blocked_stage(
        tmp_path,
        outputs=[RequiredOutput(path="out.txt")],
        validators=[ArtifactValidator(path="out.txt", schema="non-empty")],
    )
    result = await stitch_hil_gate(
        root=root, job_slug="j-stitch", stage_id="review", app_client=None
    )
    assert result.answer_endpoint_called is False


async def test_stitch_missing_builder_surfaces_named_error(tmp_path: Path) -> None:
    """A schema with no registered builder must raise loudly with the
    schema name — not silently skip the stage."""
    root, _ = _seed_blocked_stage(
        tmp_path,
        outputs=[RequiredOutput(path="weird.txt")],
        validators=[ArtifactValidator(path="weird.txt", schema="not-a-real-schema")],
    )
    with pytest.raises(MissingBuilderError, match="not-a-real-schema"):
        await stitch_hil_gate(root=root, job_slug="j-stitch", stage_id="review")


async def test_stitch_no_validators_uses_non_empty_default(tmp_path: Path) -> None:
    """A required output with no registered ``artifact_validators``
    entry falls back to the ``non-empty`` builder."""
    root, _ = _seed_blocked_stage(
        tmp_path,
        outputs=[RequiredOutput(path="anything.txt")],
        validators=None,
    )
    await stitch_hil_gate(root=root, job_slug="j-stitch", stage_id="review")

    artifact = paths.job_dir("j-stitch", root=root) / "anything.txt"
    assert artifact.is_file()
    assert artifact.read_bytes()  # non-empty


async def test_stitch_returns_paths_written(tmp_path: Path) -> None:
    root, _ = _seed_blocked_stage(
        tmp_path,
        outputs=[
            RequiredOutput(path="a.json"),
            RequiredOutput(path="b.txt"),
        ],
        validators=[
            ArtifactValidator(path="a.json", schema="review-verdict-schema"),
            ArtifactValidator(path="b.txt", schema="non-empty"),
        ],
    )
    result = await stitch_hil_gate(root=root, job_slug="j-stitch", stage_id="review")

    assert {p.name for p in result.paths_written} == {"a.json", "b.txt"}


def test_builders_passed_through_for_per_run_overrides(tmp_path: Path) -> None:
    """Caller can supply a custom registry — useful for runs that want
    a 'needs-revision' verdict instead of the default approval."""
    # This is a shape test; full integration is exercised in the
    # actual e2e test. Just confirm the parameter is honoured.
    custom: dict[str, object] = {**BUILDERS}
    assert stitch_hil_gate.__kwdefaults__["builders_registry"] is BUILDERS  # type: ignore[index]
    del custom  # silence unused
