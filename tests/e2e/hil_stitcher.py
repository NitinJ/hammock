"""HIL gate stitching for the e2e tests.

Per docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step F: when
the JobDriver blocks a stage on a human gate, the test stitches the
gate by:

1. Loading the stage's required outputs from ``stage-list.yaml``.
2. For each output, dispatching to the schema-keyed fixture-builder
   registry (``tests.e2e.hil_builders``) and writing the payload
   to disk.
3. Marking ``stage.json`` ``SUCCEEDED`` so the resumed driver skips
   the stage on the next pass.
4. (Optional) calling ``POST /api/hil/{id}/answer`` for record
   fidelity — the new contract that P5 made resolvable. Skipped when
   no ``app_client`` is supplied (the existing fake e2e operates
   this way).

The ordering matters: disk writes happen *before* the answer POST so
the cache (loaded by the lifespan startup) is populated when the
endpoint walks it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from shared import paths
from shared.atomic import atomic_write_json
from shared.models.hil import HilItem
from shared.models.stage import (
    ArtifactValidator,
    RequiredOutput,
    StageDefinition,
    StageRun,
    StageState,
)
from tests.e2e.hil_builders import BUILDERS, Builder, BuilderContext, build


@dataclass(frozen=True)
class StitchResult:
    stage_id: str
    paths_written: list[Path] = field(default_factory=list)
    answer_endpoint_called: bool = False
    answered_item_id: str | None = None


class _ResponseLike(Protocol):
    status_code: int
    text: str


class _ClientLike(Protocol):
    def post(self, path: str, json: dict[str, Any]) -> _ResponseLike: ...


def _load_stage_def(root: Path, job_slug: str, stage_id: str) -> StageDefinition:
    stage_list_path = paths.job_dir(job_slug, root=root) / "stage-list.yaml"
    data = yaml.safe_load(stage_list_path.read_text()) or {}
    for raw in data.get("stages", []):
        if isinstance(raw, dict) and raw.get("id") == stage_id:
            return StageDefinition.model_validate(raw)
    raise AssertionError(f"stage {stage_id!r} not found in stage-list.yaml at {stage_list_path}")


def _schema_for(output: RequiredOutput, validators: list[ArtifactValidator] | None) -> str:
    """Return the schema name for *output*, defaulting to ``non-empty``
    when no artifact_validators entry is registered."""
    for v in validators or []:
        if v.path == output.path:
            return v.schema_
    return "non-empty"


async def stitch_hil_gate(
    *,
    root: Path,
    job_slug: str,
    stage_id: str,
    builders_registry: dict[str, Builder] = BUILDERS,
    app_client: _ClientLike | None = None,
) -> StitchResult:
    """Resolve a BLOCKED_ON_HUMAN gate per spec D4.

    Returns a :class:`StitchResult` describing what was written.
    """
    del builders_registry  # registry is consulted via build(); kept as a
    # parameter for symmetry / future per-run override hook.

    stage_def = _load_stage_def(root, job_slug, stage_id)
    job_dir = paths.job_dir(job_slug, root=root)

    # 1. Write each required output via the schema-keyed builder.
    written: list[Path] = []
    required_outputs = stage_def.exit_condition.required_outputs or []
    validators = stage_def.exit_condition.artifact_validators
    for output in required_outputs:
        schema = _schema_for(output, validators)
        ctx = BuilderContext(
            job_dir=job_dir,
            stage_id=stage_id,
            output_path=output.path,
            schema=schema,
        )
        payload = build(schema, ctx)
        artifact_path = job_dir / output.path
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(payload)
        written.append(artifact_path)

    # 2. Mark stage SUCCEEDED.
    sj_path = paths.stage_json(job_slug, stage_id, root=root)
    if sj_path.exists():
        existing = StageRun.model_validate_json(sj_path.read_text())
        attempt = existing.attempt
    else:
        attempt = 1
    resolved = StageRun(
        stage_id=stage_id,
        attempt=attempt,
        state=StageState.SUCCEEDED,
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        cost_accrued=0.0,
    )
    atomic_write_json(sj_path, resolved)

    # 3. (Optional) POST /api/hil/{id}/answer for record fidelity.
    answer_called = False
    item_id: str | None = None
    if app_client is not None:
        item = _find_awaiting_hil_item(root, job_slug, stage_id)
        if item is not None:
            item_id = item.id
            answer_payload = _default_manual_step_answer()
            # The dashboard's cache is populated at lifespan startup; HIL
            # items written by the JobDriver after that point only reach
            # cache via the filesystem watcher (off in test mode) or a
            # rescan. Force a rescan from disk so the answer endpoint
            # sees the fresh item — caught during the real-claude e2e
            # dogfood: cache miss → 404.
            app_obj = getattr(app_client, "app", None)
            cache = getattr(getattr(app_obj, "state", None), "cache", None)
            scan = getattr(cache, "_scan", None)
            if callable(scan):
                scan(root)
            resp = app_client.post(f"/api/hil/{item.id}/answer", json=answer_payload)
            answer_called = True
            if resp.status_code != 200:
                raise AssertionError(
                    f"POST /api/hil/{item.id}/answer returned {resp.status_code}: {resp.text}"
                )

    return StitchResult(
        stage_id=stage_id,
        paths_written=written,
        answer_endpoint_called=answer_called,
        answered_item_id=item_id,
    )


def _find_awaiting_hil_item(root: Path, job_slug: str, stage_id: str) -> HilItem | None:
    hil_dir = paths.job_dir(job_slug, root=root) / "hil"
    if not hil_dir.is_dir():
        return None
    for path in sorted(hil_dir.glob("*.json")):
        try:
            item = HilItem.model_validate_json(path.read_text())
        except (OSError, ValueError):
            continue
        if item.stage_id == stage_id and item.status == "awaiting":
            return item
    return None


def _default_manual_step_answer() -> dict[str, str]:
    """Default answer payload for stage-block HilItems.

    Matches the production ``ManualStepAnswer`` schema; the e2e test
    isn't trying to exercise UI-driven answers, just the endpoint.
    """
    return {"kind": "manual-step", "output": "e2e stitch: resolved"}
