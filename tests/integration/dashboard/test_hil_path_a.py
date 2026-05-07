"""HIL Path A — explicit human-actor node round-trip.

Stage 3 delivery: ``FakeEngine.request_hil`` writes a pending marker,
the dashboard's ``GET /api/hil/{slug}/{node}`` exposes it, the
``POST .../answer`` thin handler calls
``engine.v1.hil.submit_hil_answer`` which removes the pending marker
and writes the variable envelope.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shared.atomic import atomic_write_text
from shared.v1 import paths as v1_paths
from shared.v1.envelope import make_envelope
from shared.v1.job import make_job_config
from tests.integration.conftest import DashboardHandle
from tests.integration.fake_engine import FakeEngine

_TINY_WORKFLOW_YAML = """\
schema_version: 1
workflow: t-hil-path-a

variables:
  request: { type: job-request }
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


def _start_job_with_workflow(fake_engine: FakeEngine) -> Path:
    """Lay down a minimal v1 job whose workflow.yaml the dashboard's
    HIL handler can load."""
    workflow_path = v1_paths.job_dir(fake_engine.job_slug, root=fake_engine.root) / "workflow.yaml"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(_TINY_WORKFLOW_YAML)
    v1_paths.ensure_job_layout(fake_engine.job_slug, root=fake_engine.root)
    cfg = make_job_config(
        job_slug=fake_engine.job_slug,
        workflow_name="t-hil-path-a",
        workflow_path=workflow_path,
        repo_slug=None,
    )
    atomic_write_text(
        v1_paths.job_config_path(fake_engine.job_slug, root=fake_engine.root),
        cfg.model_dump_json(),
    )
    # Seed the upstream `spec` envelope so the resolver finds it.
    spec_env = make_envelope(
        type_name="design-spec",
        producer_node="write-spec",
        value_payload={"title": "T", "overview": "the spec", "document": "## D\n\nthe spec"},
    )
    atomic_write_text(
        v1_paths.variable_envelope_path(fake_engine.job_slug, "spec", root=fake_engine.root),
        spec_env.model_dump_json(),
    )
    return workflow_path


@pytest.mark.asyncio
async def test_review_verdict_round_trip(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """Pending → GET (sees gate) → POST answer → pending gone, envelope on disk."""
    _start_job_with_workflow(fake_engine)
    fake_engine.request_hil(
        "review-spec-human",
        "review-verdict",
        output_var_names=["review"],
    )

    resp = await dashboard.client.get(f"/api/hil/{fake_engine.job_slug}/review-spec-human")
    assert resp.status_code == 200
    item = resp.json()
    assert item["node_id"] == "review-spec-human"
    assert "review" in item["output_var_names"]

    answer = {
        "var_name": "review",
        "value": {
            "verdict": "approved",
            "summary": "lgtm",
            "document": "## Review\n\nlgtm",
        },
    }
    resp = await dashboard.client.post(
        f"/api/hil/{fake_engine.job_slug}/review-spec-human/answer", json=answer
    )
    assert resp.status_code == 200, resp.text

    pending = (
        v1_paths.job_dir(fake_engine.job_slug, root=fake_engine.root)
        / "pending"
        / "review-spec-human.json"
    )
    assert not pending.exists()

    env_path = v1_paths.variable_envelope_path(
        fake_engine.job_slug, "review", root=fake_engine.root
    )
    assert env_path.exists()
    env = json.loads(env_path.read_text())
    assert env["type"] == "review-verdict"
    assert env["value"]["verdict"] == "approved"


@pytest.mark.asyncio
async def test_post_answer_404_for_missing_job(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    resp = await dashboard.client.post(
        f"/api/hil/{fake_engine.job_slug}/some-node/answer",
        json={"var_name": "x", "value": {}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_invalid_payload_is_rejected_pending_remains(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    _start_job_with_workflow(fake_engine)
    fake_engine.request_hil(
        "review-spec-human",
        "review-verdict",
        output_var_names=["review"],
    )

    # Missing `summary` is required for review-verdict — engine.produce rejects.
    resp = await dashboard.client.post(
        f"/api/hil/{fake_engine.job_slug}/review-spec-human/answer",
        json={"var_name": "review", "value": {"verdict": "approved"}},
    )
    assert resp.status_code == 400
    pending = (
        v1_paths.job_dir(fake_engine.job_slug, root=fake_engine.root)
        / "pending"
        / "review-spec-human.json"
    )
    assert pending.exists()


@pytest.mark.asyncio
async def test_get_hil_lists_pending_for_job(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    _start_job_with_workflow(fake_engine)
    fake_engine.request_hil(
        "review-spec-human",
        "review-verdict",
        output_var_names=["review"],
    )

    resp = await dashboard.client.get(f"/api/hil/{fake_engine.job_slug}")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["node_id"] == "review-spec-human"


@pytest.mark.asyncio
async def test_loop_indexed_hil_round_trip(
    dashboard: DashboardHandle, fake_engine: FakeEngine
) -> None:
    """A loop-indexed pending HIL surfaces with iter=[N] and answers
    write the loop-indexed envelope path."""
    # Reuse the path-A workflow but request HIL inside a loop iteration.
    workflow_path = v1_paths.job_dir(fake_engine.job_slug, root=fake_engine.root) / "workflow.yaml"
    workflow_path.parent.mkdir(parents=True, exist_ok=True)
    workflow_path.write_text(
        """\
schema_version: 1
workflow: t-loop-hil

variables:
  request: { type: job-request }
  spec:    { type: design-spec }
  review:  { type: review-verdict }

nodes:
  - id: review-loop
    kind: loop
    count: 1
    body:
      - id: review-spec-human
        kind: artifact
        actor: human
        inputs: { spec: $spec }
        outputs: { review: $review }
        presentation: { title: "Review" }
    outputs:
      reviews: $review-loop.review[*]
"""
    )
    v1_paths.ensure_job_layout(fake_engine.job_slug, root=fake_engine.root)
    cfg = make_job_config(
        job_slug=fake_engine.job_slug,
        workflow_name="t-loop-hil",
        workflow_path=workflow_path,
        repo_slug=None,
    )
    atomic_write_text(
        v1_paths.job_config_path(fake_engine.job_slug, root=fake_engine.root),
        cfg.model_dump_json(),
    )
    spec_env = make_envelope(
        type_name="design-spec",
        producer_node="write-spec",
        value_payload={"title": "T", "overview": "the spec", "document": "## D\n\nthe spec"},
    )
    atomic_write_text(
        v1_paths.variable_envelope_path(fake_engine.job_slug, "spec", root=fake_engine.root),
        spec_env.model_dump_json(),
    )

    fake_engine.request_hil(
        "review-spec-human",
        "review-verdict",
        output_var_names=["review"],
        iter=(0,),
        loop_id="review-loop",
    )

    resp = await dashboard.client.get(f"/api/hil/{fake_engine.job_slug}")
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "explicit"
    assert item["iter"] == [0]

    answer = {
        "var_name": "review",
        "value": {"verdict": "approved", "summary": "ok", "document": "## Review\n\nok"},
    }
    resp = await dashboard.client.post(
        f"/api/hil/{fake_engine.job_slug}/review-spec-human/answer", json=answer
    )
    assert resp.status_code == 200, resp.text

    # Loop-indexed envelope path lands on disk.
    env_path = v1_paths.loop_variable_envelope_path(
        fake_engine.job_slug, "review-loop", "review", 0, root=fake_engine.root
    )
    assert env_path.is_file()
    env = json.loads(env_path.read_text())
    assert env["type"] == "review-verdict"
    assert env["value"]["verdict"] == "approved"
