"""Unit tests for engine/v1/resolver.py."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from engine.v1.resolver import (
    ResolutionError,
    _parse_ref,
    _strip_optional_suffix,
    resolve_node_inputs,
)
from shared.v1 import paths
from shared.v1.envelope import make_envelope
from shared.v1.types.bug_report import BugReportValue
from shared.v1.types.job_request import JobRequestValue
from shared.v1.workflow import ArtifactNode, VariableSpec, Workflow


def _write_envelope(
    *,
    root: Path,
    job_slug: str,
    var_name: str,
    type_name: str,
    value: dict[str, object],
    producer: str = "n",
) -> None:
    paths.ensure_job_layout(job_slug, root=root)
    env = make_envelope(
        type_name=type_name,
        producer_node=producer,
        value_payload=value,
        now=datetime.now(UTC),
    )
    paths.variable_envelope_path(job_slug, var_name, root=root).write_text(env.model_dump_json())


# ---------------------------------------------------------------------------
# _strip_optional_suffix
# ---------------------------------------------------------------------------


def test_strip_optional_suffix_present() -> None:
    assert _strip_optional_suffix("prior_review?") == ("prior_review", True)


def test_strip_optional_suffix_absent() -> None:
    assert _strip_optional_suffix("design_spec") == ("design_spec", False)


# ---------------------------------------------------------------------------
# _parse_ref
# ---------------------------------------------------------------------------


def test_parse_ref_simple() -> None:
    assert _parse_ref("$bug_report") == ("bug_report", [])


def test_parse_ref_field_access() -> None:
    assert _parse_ref("$bug_report.summary") == ("bug_report", ["summary"])


def test_parse_ref_nested_field_access() -> None:
    assert _parse_ref("$plan.stages.first.title") == (
        "plan",
        ["stages", "first", "title"],
    )


def test_parse_ref_malformed_raises() -> None:
    with pytest.raises(ResolutionError, match="malformed"):
        _parse_ref("not-a-var-ref")


# ---------------------------------------------------------------------------
# resolve_node_inputs — happy path (single-variable consumption)
# ---------------------------------------------------------------------------


def test_resolves_required_input_to_value_model(tmp_path: Path) -> None:
    job_slug = "j1"
    _write_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="bug_report",
        type_name="bug-report",
        value={"summary": "x", "document": "## Bug\n\nx"},
    )
    wf = Workflow(
        schema_version=1,
        workflow="t",
        variables={"bug_report": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="consumer",
                kind="artifact",
                actor="agent",
                inputs={"r": "$bug_report"},
                outputs={},
            )
        ],
    )
    resolved = resolve_node_inputs(node=wf.nodes[0], workflow=wf, job_slug=job_slug, root=tmp_path)
    assert "r" in resolved
    slot = resolved["r"]
    assert slot.present is True
    assert isinstance(slot.value, BugReportValue)
    assert slot.value.summary == "x"


def test_required_missing_variable_raises(tmp_path: Path) -> None:
    paths.ensure_job_layout("j1", root=tmp_path)
    wf = Workflow(
        schema_version=1,
        workflow="t",
        variables={"missing": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="consumer",
                kind="artifact",
                actor="agent",
                inputs={"r": "$missing"},
                outputs={},
            )
        ],
    )
    with pytest.raises(ResolutionError, match="not been produced"):
        resolve_node_inputs(node=wf.nodes[0], workflow=wf, job_slug="j1", root=tmp_path)


def test_optional_missing_variable_yields_absent_slot(tmp_path: Path) -> None:
    paths.ensure_job_layout("j1", root=tmp_path)
    wf = Workflow(
        schema_version=1,
        workflow="t",
        variables={"prior_review": VariableSpec(type="review-verdict")},
        nodes=[
            ArtifactNode(
                id="consumer",
                kind="artifact",
                actor="agent",
                inputs={"prior_review?": "$prior_review"},
                outputs={},
            )
        ],
    )
    resolved = resolve_node_inputs(node=wf.nodes[0], workflow=wf, job_slug="j1", root=tmp_path)
    assert resolved["prior_review"].present is False
    assert resolved["prior_review"].value is None
    assert resolved["prior_review"].optional is True


def test_field_access_returns_primitive(tmp_path: Path) -> None:
    job_slug = "j1"
    _write_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="request",
        type_name="job-request",
        value={"text": "Fix the bug"},
    )
    wf = Workflow(
        schema_version=1,
        workflow="t",
        variables={"request": VariableSpec(type="job-request")},
        nodes=[
            ArtifactNode(
                id="consumer",
                kind="artifact",
                actor="agent",
                inputs={"text": "$request.text"},
                outputs={},
            )
        ],
    )
    resolved = resolve_node_inputs(node=wf.nodes[0], workflow=wf, job_slug=job_slug, root=tmp_path)
    assert resolved["text"].value == "Fix the bug"


def test_field_access_unknown_field_raises(tmp_path: Path) -> None:
    job_slug = "j1"
    _write_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="request",
        type_name="job-request",
        value={"text": "x"},
    )
    wf = Workflow(
        schema_version=1,
        workflow="t",
        variables={"request": VariableSpec(type="job-request")},
        nodes=[
            ArtifactNode(
                id="consumer",
                kind="artifact",
                actor="agent",
                inputs={"x": "$request.no_such_field"},
                outputs={},
            )
        ],
    )
    with pytest.raises(ResolutionError, match="no field 'no_such_field'"):
        resolve_node_inputs(node=wf.nodes[0], workflow=wf, job_slug=job_slug, root=tmp_path)


def test_resolves_multiple_inputs(tmp_path: Path) -> None:
    job_slug = "j1"
    _write_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="request",
        type_name="job-request",
        value={"text": "the request"},
    )
    _write_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="bug_report",
        type_name="bug-report",
        value={"summary": "the bug", "document": "## Bug\n\nthe bug"},
    )
    wf = Workflow(
        schema_version=1,
        workflow="t",
        variables={
            "request": VariableSpec(type="job-request"),
            "bug_report": VariableSpec(type="bug-report"),
        },
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                inputs={"req": "$request", "bug": "$bug_report"},
                outputs={},
            )
        ],
    )
    resolved = resolve_node_inputs(node=wf.nodes[0], workflow=wf, job_slug=job_slug, root=tmp_path)
    assert resolved["req"].present
    assert resolved["bug"].present
    assert isinstance(resolved["req"].value, JobRequestValue)
    assert isinstance(resolved["bug"].value, BugReportValue)


def test_resolver_follows_multi_hop_ref_chain(tmp_path: Path) -> None:
    """Nested loops with `[last]`/single-iter projections naturally
    produce chains of $ref pointer files: outer-loop projection points
    at inner-loop projection points at the body's actual envelope.
    Resolver must follow the chain to its terminating envelope, not
    raise on the second hop. T6 dogfood discovery, 2026-05-08.
    """
    from engine.v1.resolver import _read_envelope

    job_slug = "test-multi-hop"
    paths.ensure_job_layout(job_slug, root=tmp_path)
    # Body's actual envelope at innermost iter path (outer=0, inner=0).
    _write_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="design_spec",
        type_name="bug-report",
        value={"summary": "from-body", "document": "## body content"},
    )
    body_path = paths.variable_envelope_path(job_slug, "design_spec", root=tmp_path)
    body_path.rename(body_path.parent / "design_spec__i0_0.json")
    # Inner loop's projection at outer iter 0.
    (body_path.parent / "design_spec__i0.json").write_text('{"$ref": "design_spec__i0_0"}')
    # Outer loop's projection at top scope.
    (body_path.parent / "design_spec__top.json").write_text('{"$ref": "design_spec__i0"}')

    env = _read_envelope(body_path.parent / "design_spec__top.json")
    assert env is not None
    assert env.value["summary"] == "from-body"


def test_resolver_detects_ref_pointer_cycle(tmp_path: Path) -> None:
    """A → B → A cycle is malformed and must raise rather than loop."""
    from engine.v1.resolver import _read_envelope

    job_slug = "test-cycle"
    paths.ensure_job_layout(job_slug, root=tmp_path)
    var_dir = paths.variables_dir(job_slug, root=tmp_path)
    (var_dir / "x__top.json").write_text('{"$ref": "x__i0"}')
    (var_dir / "x__i0.json").write_text('{"$ref": "x__top"}')

    with pytest.raises(ResolutionError, match="cycle detected"):
        _read_envelope(var_dir / "x__top.json")
