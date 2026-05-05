"""Unit tests for tests/e2e_v1/outcomes.py."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from shared.v1 import paths
from shared.v1.envelope import make_envelope
from shared.v1.job import (
    JobState,
    NodeRunState,
    make_job_config,
    make_node_run,
)
from shared.v1.workflow import ArtifactNode, VariableSpec, Workflow
from tests.e2e_v1.outcomes import (
    assert_all_declared_outputs_produced,
    assert_all_nodes_succeeded_or_skipped,
    assert_envelopes_well_formed,
    assert_job_completed,
    assert_node_artefacts_present,
)


def _t1_workflow() -> Workflow:
    return Workflow(
        workflow="t1",
        variables={
            "request": VariableSpec(type="job-request"),
            "bug_report": VariableSpec(type="bug-report"),
            "design_spec": VariableSpec(type="design-spec"),
        },
        nodes=[
            ArtifactNode(
                id="write-bug-report",
                kind="artifact",
                actor="agent",
                inputs={"request": "$request"},
                outputs={"bug_report": "$bug_report"},
            ),
            ArtifactNode(
                id="write-design-spec",
                kind="artifact",
                actor="agent",
                after=["write-bug-report"],
                inputs={"bug_report": "$bug_report"},
                outputs={"design_spec": "$design_spec"},
            ),
        ],
    )


def _seed_job(*, root: Path, job_slug: str, state: JobState) -> None:
    paths.ensure_job_layout(job_slug, root=root)
    cfg = make_job_config(
        job_slug=job_slug,
        workflow_name="t1",
        workflow_path=root / "wf.yaml",
        repo_slug=None,
    )
    cfg = cfg.model_copy(update={"state": state})
    paths.job_config_path(job_slug, root=root).write_text(cfg.model_dump_json(indent=2))


def _seed_envelope(
    *, root: Path, job_slug: str, var_name: str, type_name: str, value: dict[str, object]
) -> None:
    paths.ensure_job_layout(job_slug, root=root)
    env = make_envelope(
        type_name=type_name,
        producer_node="<test>",
        value_payload=value,
    )
    paths.variable_envelope_path(job_slug, var_name, root=root).write_text(env.model_dump_json())


def _seed_node_run(
    *,
    root: Path,
    job_slug: str,
    node_id: str,
    state: NodeRunState,
    attempt: int = 1,
) -> None:
    paths.node_dir(job_slug, node_id, root=root).mkdir(parents=True, exist_ok=True)
    run = make_node_run(node_id)
    run = run.model_copy(
        update={
            "state": state,
            "attempts": attempt,
            "started_at": datetime.now(UTC),
            "finished_at": datetime.now(UTC),
        }
    )
    paths.node_state_path(job_slug, node_id, root=root).write_text(run.model_dump_json())
    # Also seed the attempt dir + conventional files.
    attempt_dir = paths.node_attempt_dir(job_slug, node_id, attempt, root=root)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "prompt.md").write_text("(prompt)")
    (attempt_dir / "stdout.log").write_text("ok")
    (attempt_dir / "stderr.log").write_text("")


# ---------------------------------------------------------------------------
# assert_job_completed
# ---------------------------------------------------------------------------


def test_job_completed_passes(tmp_path: Path) -> None:
    _seed_job(root=tmp_path, job_slug="j", state=JobState.COMPLETED)
    assert_job_completed(tmp_path, "j", _t1_workflow())


def test_job_completed_fails_when_state_not_completed(tmp_path: Path) -> None:
    _seed_job(root=tmp_path, job_slug="j", state=JobState.RUNNING)
    with pytest.raises(AssertionError, match="did not reach COMPLETED"):
        assert_job_completed(tmp_path, "j", _t1_workflow())


def test_job_completed_fails_when_config_missing(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    with pytest.raises(AssertionError, match="job config missing"):
        assert_job_completed(tmp_path, "j", _t1_workflow())


# ---------------------------------------------------------------------------
# assert_all_declared_outputs_produced
# ---------------------------------------------------------------------------


def test_all_outputs_produced_passes(tmp_path: Path) -> None:
    _seed_envelope(
        root=tmp_path,
        job_slug="j",
        var_name="bug_report",
        type_name="bug-report",
        value={"summary": "x"},
    )
    _seed_envelope(
        root=tmp_path,
        job_slug="j",
        var_name="design_spec",
        type_name="design-spec",
        value={"title": "t", "overview": "o"},
    )
    assert_all_declared_outputs_produced(tmp_path, "j", _t1_workflow())


def test_all_outputs_produced_fails_when_one_missing(tmp_path: Path) -> None:
    _seed_envelope(
        root=tmp_path,
        job_slug="j",
        var_name="bug_report",
        type_name="bug-report",
        value={"summary": "x"},
    )
    # design_spec missing
    with pytest.raises(AssertionError, match=r"design_spec.*missing"):
        assert_all_declared_outputs_produced(tmp_path, "j", _t1_workflow())


def test_all_outputs_produced_ignores_optional_missing(tmp_path: Path) -> None:
    """Optional outputs (`?`) absent is OK."""
    wf = Workflow(
        workflow="w",
        variables={"x": VariableSpec(type="bug-report")},
        nodes=[
            ArtifactNode(
                id="n",
                kind="artifact",
                actor="agent",
                inputs={},
                outputs={"x?": "$x"},
            )
        ],
    )
    paths.ensure_job_layout("j", root=tmp_path)
    assert_all_declared_outputs_produced(tmp_path, "j", wf)


# ---------------------------------------------------------------------------
# assert_envelopes_well_formed
# ---------------------------------------------------------------------------


def test_envelopes_well_formed_passes(tmp_path: Path) -> None:
    _seed_envelope(
        root=tmp_path,
        job_slug="j",
        var_name="bug_report",
        type_name="bug-report",
        value={"summary": "x"},
    )
    assert_envelopes_well_formed(tmp_path, "j", _t1_workflow())


def test_envelopes_well_formed_fails_on_corrupt_json(tmp_path: Path) -> None:
    paths.ensure_job_layout("j", root=tmp_path)
    paths.variable_envelope_path("j", "bug_report", root=tmp_path).write_text("{ broken")
    with pytest.raises(AssertionError, match="schema validation"):
        assert_envelopes_well_formed(tmp_path, "j", _t1_workflow())


def test_envelopes_well_formed_fails_on_type_mismatch(tmp_path: Path) -> None:
    """An envelope claims one type, but the workflow declared another for
    that variable name."""
    _seed_envelope(
        root=tmp_path,
        job_slug="j",
        var_name="bug_report",
        type_name="design-spec",  # WRONG
        value={"title": "t", "overview": "o"},
    )
    with pytest.raises(AssertionError, match="declares type"):
        assert_envelopes_well_formed(tmp_path, "j", _t1_workflow())


def test_envelopes_well_formed_fails_on_invalid_value_payload(tmp_path: Path) -> None:
    _seed_envelope(
        root=tmp_path,
        job_slug="j",
        var_name="bug_report",
        type_name="bug-report",
        value={"summary": ""},  # empty summary violates min_length=1
    )
    with pytest.raises(AssertionError, match="type schema"):
        assert_envelopes_well_formed(tmp_path, "j", _t1_workflow())


# ---------------------------------------------------------------------------
# assert_all_nodes_succeeded_or_skipped
# ---------------------------------------------------------------------------


def test_all_nodes_succeeded_passes(tmp_path: Path) -> None:
    for node_id in ("write-bug-report", "write-design-spec"):
        _seed_node_run(
            root=tmp_path,
            job_slug="j",
            node_id=node_id,
            state=NodeRunState.SUCCEEDED,
        )
    assert_all_nodes_succeeded_or_skipped(tmp_path, "j", _t1_workflow())


def test_all_nodes_fails_on_failed_node(tmp_path: Path) -> None:
    _seed_node_run(
        root=tmp_path,
        job_slug="j",
        node_id="write-bug-report",
        state=NodeRunState.FAILED,
    )
    with pytest.raises(AssertionError, match="state failed"):
        assert_all_nodes_succeeded_or_skipped(tmp_path, "j", _t1_workflow())


def test_all_nodes_fails_when_state_file_missing_for_unconditional_node(
    tmp_path: Path,
) -> None:
    # T1 has no runs_if, so absence of state.json for any node is failure.
    paths.ensure_job_layout("j", root=tmp_path)
    with pytest.raises(AssertionError, match="never ran"):
        assert_all_nodes_succeeded_or_skipped(tmp_path, "j", _t1_workflow())


# ---------------------------------------------------------------------------
# assert_node_artefacts_present
# ---------------------------------------------------------------------------


def test_node_artefacts_present_passes(tmp_path: Path) -> None:
    _seed_node_run(
        root=tmp_path,
        job_slug="j",
        node_id="write-bug-report",
        state=NodeRunState.SUCCEEDED,
    )
    _seed_node_run(
        root=tmp_path,
        job_slug="j",
        node_id="write-design-spec",
        state=NodeRunState.SUCCEEDED,
    )
    assert_node_artefacts_present(tmp_path, "j", _t1_workflow())


def test_node_artefacts_fails_on_missing_prompt(tmp_path: Path) -> None:
    _seed_node_run(
        root=tmp_path,
        job_slug="j",
        node_id="write-bug-report",
        state=NodeRunState.SUCCEEDED,
    )
    # delete prompt.md
    (paths.node_attempt_dir("j", "write-bug-report", 1, root=tmp_path) / "prompt.md").unlink()
    _seed_node_run(
        root=tmp_path,
        job_slug="j",
        node_id="write-design-spec",
        state=NodeRunState.SUCCEEDED,
    )
    with pytest.raises(AssertionError, match="missing artefact"):
        assert_node_artefacts_present(tmp_path, "j", _t1_workflow())
