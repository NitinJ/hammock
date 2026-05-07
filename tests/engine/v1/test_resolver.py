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
