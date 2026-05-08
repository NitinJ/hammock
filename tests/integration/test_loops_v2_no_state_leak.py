"""T7 regression — loops-v2 no-state-leak invariant.

The bug class loops-v2 was built to kill: when an outer until-loop
re-enters its body for a new iteration, the body nodes' state and
variable envelopes for the previous iteration must NOT be overwritten or
otherwise lost. Today's keying — every (node_id, iter_path) gets its own
on-disk path — makes this structural rather than a heuristic.

This test drives a 2-iter outer until-loop with body nodes
``write-design-spec`` (artifact agent) and ``review-design-spec-human``
(modelled as an artifact agent for test simplicity — the no-state-leak
property is independent of actor). Verdict pattern:

- iter 0: ``needs-revision`` → outer loop re-enters
- iter 1: ``approved``       → outer loop exits

Asserted invariants after the dispatcher returns:

1. Body nodes have iter-keyed state files at BOTH ``i0`` and ``i1`` —
   neither overwrote the other; their content is distinct.
2. Variable envelopes for the body output ``design_spec`` exist at
   distinct paths ``design_spec__i0.json`` and ``design_spec__i1.json``
   with distinct content (the iter-1 spec was not stomped onto the
   iter-0 path).
3. The outer-scope projection at ``variables/design_spec__top.json`` is
   exactly ``{"$ref": "design_spec__i1"}`` — text match, the lazy
   pointer-file shape, not an envelope copy.
4. The resolver, given ``$design_spec`` from a top-level node's
   perspective, returns the iter-1 envelope by transparently following
   the ``$ref`` indirection.

Together these prevent re-introduction of every variant of the
"outer-iter advances and the inner state quietly fuses with the previous
iter's state" bug we kept paying for through dogfood-fixes-1, -2, and
the iter_path retrofit.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from engine.v1.loop_dispatch import dispatch_loop
from engine.v1.resolver import resolve_node_inputs
from shared.v1 import paths
from shared.v1.envelope import Envelope
from shared.v1.job import NodeRun, NodeRunState
from shared.v1.types.design_spec import DesignSpecValue
from shared.v1.types.review_verdict import ReviewVerdictValue
from shared.v1.workflow import (
    ArtifactNode,
    LoopNode,
    VariableSpec,
    Workflow,
)


def _t7_workflow() -> Workflow:
    """Outer until-loop, body = write-design-spec then
    review-design-spec-human, until the review verdict is 'approved'."""
    return Workflow(
        schema_version=1,
        workflow="t7-no-state-leak",
        variables={
            "design_spec": VariableSpec(type="design-spec"),
            "design_spec_review_human": VariableSpec(type="review-verdict"),
        },
        nodes=[
            LoopNode(
                id="design-spec-loop",
                kind="loop",
                until=("$design-spec-loop.design_spec_review_human[i].verdict == 'approved'"),
                max_iterations=3,
                substrate="shared",
                body=[
                    ArtifactNode(
                        id="write-design-spec",
                        kind="artifact",
                        actor="agent",
                        inputs={},
                        outputs={"design_spec": "$design_spec"},
                    ),
                    ArtifactNode(
                        id="review-design-spec-human",
                        kind="artifact",
                        actor="agent",
                        after=["write-design-spec"],
                        inputs={"design_spec": "$design-spec-loop.design_spec[i]"},
                        outputs={"verdict": "$design_spec_review_human"},
                    ),
                ],
                outputs={"design_spec": "$design-spec-loop.design_spec[last]"},
            )
        ],
    )


def test_t7_outer_iter_does_not_leak_inner_state(tmp_path: Path) -> None:
    job_slug = "t7"
    paths.ensure_job_layout(job_slug, root=tmp_path)
    workflow = _t7_workflow()

    # Per-(node, iter) output recipes. The reviewer alternates verdicts
    # so the until-loop runs exactly twice.
    iter_to_design = {
        0: {
            "title": "draft 0",
            "overview": "first attempt",
            "document": "## Design v0\n\nfirst attempt",
        },
        1: {
            "title": "draft 1",
            "overview": "addressed feedback",
            "document": "## Design v1\n\naddressed feedback",
        },
    }
    iter_to_verdict = {
        0: {
            "verdict": "needs-revision",
            "summary": "iter-0: needs-revision",
            "document": "## Review iter 0\n\ngo again",
        },
        1: {
            "verdict": "approved",
            "summary": "iter-1: approved",
            "document": "## Review iter 1\n\nlgtm",
        },
    }

    # The fake claude runner picks an output by parsing the prompt's
    # node-id mention plus the attempt_dir's parent path — this lets
    # one runner serve both body nodes across two iterations.
    def fake_runner(prompt: str, attempt_dir: Path, cwd: Path | None = None):
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "chat.jsonl").write_text("")
        (attempt_dir / "stderr.log").write_text("")

        # attempt_dir is .../nodes/<node_id>/<iter_token>/runs/<n>/
        node_id = attempt_dir.parents[2].name
        iter_token = attempt_dir.parents[1].name
        # iter_token is "i0" / "i1" here (single-loop nesting).
        iter_idx = int(iter_token.removeprefix("i"))

        if node_id == "write-design-spec":
            payload = iter_to_design[iter_idx]
        elif node_id == "review-design-spec-human":
            payload = iter_to_verdict[iter_idx]
        else:
            raise AssertionError(f"unexpected node_id {node_id!r}")

        (attempt_dir / "output.json").write_text(json.dumps(payload))
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    result = dispatch_loop(
        node=workflow.nodes[0],
        workflow=workflow,
        job_slug=job_slug,
        root=tmp_path,
        job_repo=None,
        artifact_claude_runner=fake_runner,
    )

    assert result.succeeded, result.error
    # Two iterations: needs-revision → re-enter → approved → exit.
    assert result.iterations_run == 2

    # ------------------------------------------------------------------
    # Invariant 1 — both body nodes have distinct iter-keyed state files.
    # An overwrite would either: leave only one file, fuse them, or have
    # identical content. None of those is allowed.
    # ------------------------------------------------------------------
    for node_id in ("write-design-spec", "review-design-spec-human"):
        s0 = paths.node_state_path(job_slug, node_id, (0,), root=tmp_path)
        s1 = paths.node_state_path(job_slug, node_id, (1,), root=tmp_path)
        assert s0.is_file(), f"iter-0 state file missing for {node_id} at {s0}"
        assert s1.is_file(), f"iter-1 state file missing for {node_id} at {s1}"
        run0 = NodeRun.model_validate_json(s0.read_text())
        run1 = NodeRun.model_validate_json(s1.read_text())
        assert run0.state == NodeRunState.SUCCEEDED
        assert run1.state == NodeRunState.SUCCEEDED
        # Both iter-state files write a finished_at distinct from each
        # other — the iter-0 file was NOT clobbered when iter-1 ran.
        assert run0.finished_at is not None
        assert run1.finished_at is not None
        # The iter-1 NodeRun's started_at is strictly later than iter-0's
        # finished_at — ordering preserved across iterations.
        assert run0.started_at is not None
        assert run1.started_at is not None
        assert run1.started_at >= run0.finished_at, (
            f"{node_id}: iter-1 started_at ({run1.started_at!r}) precedes iter-0 "
            f"finished_at ({run0.finished_at!r}) — looks like state merge"
        )
        # Top-level (legacy) flat state file MUST NOT exist for body
        # nodes. If it does, it indicates the body node was incorrectly
        # state-keyed at the top-level path.
        flat = paths.node_state_path(job_slug, node_id, (), root=tmp_path)
        assert not flat.is_file(), (
            f"body node {node_id!r} leaked a flat top-level state file at "
            f"{flat} — body nodes must only have iter-keyed state"
        )

    # ------------------------------------------------------------------
    # Invariant 2 — distinct iter-keyed envelope files with distinct
    # content. The bug we're guarding against is "iter 1's design_spec
    # overwrote iter 0's design_spec" or "design_spec's path doesn't
    # encode the iter so both iters land at one path".
    # ------------------------------------------------------------------
    e0_path = paths.variable_envelope_path(job_slug, "design_spec", (0,), root=tmp_path)
    e1_path = paths.variable_envelope_path(job_slug, "design_spec", (1,), root=tmp_path)
    assert e0_path.is_file(), f"iter-0 design_spec envelope missing at {e0_path}"
    assert e1_path.is_file(), f"iter-1 design_spec envelope missing at {e1_path}"
    assert e0_path != e1_path, "iter-keyed envelope paths collapsed onto one path"
    e0 = Envelope.model_validate_json(e0_path.read_text())
    e1 = Envelope.model_validate_json(e1_path.read_text())
    e0_value = DesignSpecValue.model_validate(e0.value)
    e1_value = DesignSpecValue.model_validate(e1.value)
    assert e0_value.title == "draft 0"
    assert e1_value.title == "draft 1"
    assert e0_value.document != e1_value.document, (
        "iter-0 and iter-1 design specs have identical document content — "
        "looks like one was overwritten"
    )

    # Same property for the verdicts (the predicate's input).
    v0 = Envelope.model_validate_json(
        paths.variable_envelope_path(
            job_slug, "design_spec_review_human", (0,), root=tmp_path
        ).read_text()
    )
    v1 = Envelope.model_validate_json(
        paths.variable_envelope_path(
            job_slug, "design_spec_review_human", (1,), root=tmp_path
        ).read_text()
    )
    assert ReviewVerdictValue.model_validate(v0.value).verdict == "needs-revision"
    assert ReviewVerdictValue.model_validate(v1.value).verdict == "approved"

    # ------------------------------------------------------------------
    # Invariant 3 — outer-scope projection is the lazy $ref pointer file
    # exactly. Bytes-on-disk match: ``{"$ref": "design_spec__i1"}``.
    # An eager copy here would re-introduce the duplicate-content /
    # provenance-confusion bug.
    # ------------------------------------------------------------------
    outer_proj_path = paths.variable_envelope_path(job_slug, "design_spec", (), root=tmp_path)
    assert outer_proj_path.is_file(), (
        f"outer-scope projection for design_spec missing at {outer_proj_path}"
    )
    outer_proj_raw = outer_proj_path.read_text()
    outer_proj = json.loads(outer_proj_raw)
    assert outer_proj == {"$ref": "design_spec__i1"}, (
        f"expected exact $ref pointer at outer scope, got {outer_proj!r} (raw: {outer_proj_raw!r})"
    )

    # ------------------------------------------------------------------
    # Invariant 4 — resolver follows the $ref transparently and returns
    # the iter-1 design_spec when a downstream top-level node reads
    # ``$design_spec``.
    # ------------------------------------------------------------------
    downstream = ArtifactNode(
        id="downstream-consumer",
        kind="artifact",
        actor="agent",
        inputs={"spec": "$design_spec"},
        outputs={},
    )
    resolved = resolve_node_inputs(
        node=downstream,
        workflow=workflow,
        job_slug=job_slug,
        root=tmp_path,
        iter_path=(),
    )
    assert "spec" in resolved
    assert resolved["spec"].present
    spec_value = resolved["spec"].value
    assert isinstance(spec_value, DesignSpecValue)
    assert spec_value.title == "draft 1", (
        "resolver did not return the iter-1 design_spec via the $ref pointer"
    )
    assert spec_value.document == iter_to_design[1]["document"]
