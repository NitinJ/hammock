"""Stage 2 Step 1 — failing tests for NodeContext.inputs in submit_hil_answer.

Per design-patch §9.4: human-actor types like ``pr-review-verdict`` need
upstream variable values when their ``produce`` runs. ``NodeContext``
gains an ``inputs: dict[str, Any]`` field that ``submit_hil_answer``
populates by resolving the node's declared inputs against the variable
store on disk before invoking ``produce``.

Tests describe the contract and will fail until Step 2 wires up
``submit_hil_answer`` to populate ``ctx.inputs``. Frozen for Step 3.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.v1.hil import HilSubmissionError, submit_hil_answer, write_pending_marker
from shared.atomic import atomic_write_text
from shared.v1 import paths
from shared.v1.envelope import make_envelope
from shared.v1.workflow import Workflow


def _build_workflow_with_pr_and_review_node() -> Workflow:
    """Tiny workflow: one ArtifactNode + one human-actor node consuming
    the upstream pr variable."""
    yaml_text = """
schema_version: 1
workflow: t-pr-review-test

variables:
  request:    { type: job-request }
  pr:         { type: pr }
  pr_review:  { type: pr-review-verdict }

nodes:
  - id: emit-pr
    kind: artifact
    actor: agent
    inputs:
      request: $request
    outputs:
      pr: $pr

  - id: pr-review-hil
    kind: artifact
    actor: human
    after: [emit-pr]
    inputs:
      pr: $pr
    outputs:
      pr_review: $pr_review
    presentation:
      title: "Review the PR"
"""
    import yaml

    return Workflow.model_validate(yaml.safe_load(yaml_text))


@pytest.fixture
def seeded_pr(tmp_path: Path) -> Path:
    """Lay down the upstream `pr` envelope so submit_hil_answer's
    resolver finds it."""
    paths.ensure_job_layout("j", root=tmp_path)
    env = make_envelope(
        type_name="pr",
        producer_node="emit-pr",
        value_payload={
            "url": "https://github.com/example/repo/pull/42",
            "number": 42,
            "branch": "feat/foo",
            "base": "main",
            "repo": "example/repo",
        },
    )
    atomic_write_text(
        paths.variable_envelope_path("j", "pr", root=tmp_path),
        env.model_dump_json(),
    )
    return tmp_path


def test_submit_populates_ctx_inputs_from_node_declared_inputs(
    seeded_pr: Path,
) -> None:
    """When the human submits {verdict: merged} for the pr-review-hil
    node, submit_hil_answer must invoke pr-review-verdict.produce with
    ctx.inputs["pr"] populated from the upstream envelope."""
    wf = _build_workflow_with_pr_and_review_node()
    review_node = next(n for n in wf.nodes if n.id == "pr-review-hil")

    write_pending_marker(
        job_slug="j",
        node=review_node,  # type: ignore[arg-type]
        workflow=wf,
        root=seeded_pr,
    )

    captured: dict[str, object] = {}

    real_type = __import__(
        "shared.v1.types.pr_review_verdict", fromlist=["PRReviewVerdictType"]
    ).PRReviewVerdictType

    def _spy_produce(self, decl, ctx):  # type: ignore[no-untyped-def]
        captured["inputs"] = dict(ctx.inputs)
        from shared.v1.types.pr_review_verdict import PRReviewVerdictValue

        return PRReviewVerdictValue(verdict="merged", summary="")

    with patch.object(real_type, "produce", _spy_produce):
        submit_hil_answer(
            job_slug="j",
            node_id="pr-review-hil",
            var_name="pr_review",
            value_payload={"verdict": "merged"},
            root=seeded_pr,
            workflow=wf,
        )

    inputs = captured["inputs"]
    assert isinstance(inputs, dict)
    assert "pr" in inputs, f"ctx.inputs should contain 'pr'; got {list(inputs)}"
    pr_input = inputs["pr"]
    # The resolver materialises the envelope as a PRValue Pydantic
    # instance (or compatible structural object). It must expose the URL.
    pr_url = getattr(pr_input, "url", None) or (
        pr_input.get("url") if isinstance(pr_input, dict) else None
    )
    assert pr_url == "https://github.com/example/repo/pull/42"


def test_submit_rejects_when_required_input_missing(tmp_path: Path) -> None:
    """If the upstream `pr` envelope hasn't been produced, submit must
    fail before invoking produce — there's nothing to populate ctx.inputs
    from."""
    paths.ensure_job_layout("j", root=tmp_path)
    wf = _build_workflow_with_pr_and_review_node()
    review_node = next(n for n in wf.nodes if n.id == "pr-review-hil")
    write_pending_marker(
        job_slug="j",
        node=review_node,  # type: ignore[arg-type]
        workflow=wf,
        root=tmp_path,
    )
    # Note: no pr envelope seeded.
    with pytest.raises(HilSubmissionError):
        submit_hil_answer(
            job_slug="j",
            node_id="pr-review-hil",
            var_name="pr_review",
            value_payload={"verdict": "merged"},
            root=tmp_path,
            workflow=wf,
        )


def test_review_verdict_node_works_without_input_resolution(tmp_path: Path) -> None:
    """Backward-compatible: nodes whose type doesn't read ctx.inputs
    (e.g. plain review-verdict for spec reviews) still work — inputs
    populates with whatever the node declared, but the type ignores it."""
    paths.ensure_job_layout("j", root=tmp_path)
    yaml_text = """
schema_version: 1
workflow: t-spec-review-test

variables:
  spec:    { type: design-spec }
  review:  { type: review-verdict }

nodes:
  - id: write-spec
    kind: artifact
    actor: agent
    outputs:
      spec: $spec

  - id: review-spec-human
    kind: artifact
    actor: human
    after: [write-spec]
    inputs:
      spec: $spec
    outputs:
      review: $review
    presentation:
      title: "Review the spec"
"""
    import yaml

    wf = Workflow.model_validate(yaml.safe_load(yaml_text))

    # Seed the spec envelope.
    spec_env = make_envelope(
        type_name="design-spec",
        producer_node="write-spec",
        value_payload={"title": "Design", "overview": "the spec", "document": "## D\n\nthe spec"},
    )
    atomic_write_text(
        paths.variable_envelope_path("j", "spec", root=tmp_path),
        spec_env.model_dump_json(),
    )

    review_node = next(n for n in wf.nodes if n.id == "review-spec-human")
    write_pending_marker(
        job_slug="j",
        node=review_node,  # type: ignore[arg-type]
        workflow=wf,
        root=tmp_path,
    )

    submit_hil_answer(
        job_slug="j",
        node_id="review-spec-human",
        var_name="review",
        value_payload={"verdict": "approved", "summary": "lgtm"},
        root=tmp_path,
        workflow=wf,
    )

    # Pending marker gone (gate complete).
    pending = paths.job_dir("j", root=tmp_path) / "pending" / "review-spec-human.json"
    assert not pending.exists()

    # Envelope on disk.
    env_path = paths.variable_envelope_path("j", "review", root=tmp_path)
    assert env_path.exists()
    env_data = json.loads(env_path.read_text())
    assert env_data["value"]["verdict"] == "approved"
