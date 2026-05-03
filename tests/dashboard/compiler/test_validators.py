"""Unit tests for ``dashboard.compiler.validators``."""

from __future__ import annotations

from dashboard.compiler.validators import (
    JOB_LEVEL_INPUTS,
    validate_dag_closure,
    validate_human_stages_have_presentation,
    validate_known_validators,
    validate_loop_back_targets,
    validate_no_path_traversal,
    validate_parallel_with,
    validate_plan,
    validate_predicates,
    validate_unique_ids,
)
from shared.models import (
    ArtifactValidator,
    Budget,
    ExitCondition,
    InputSpec,
    LoopBack,
    OnExhaustion,
    OutputSpec,
    PresentationBlock,
    RequiredOutput,
    StageDefinition,
)


def _stage(
    id: str,
    *,
    worker: str = "agent",
    inputs: list[str] | None = None,
    optional_inputs: list[str] | None = None,
    outputs: list[str] | None = None,
    loop_back: LoopBack | None = None,
    parallel_with: list[str] | None = None,
    presentation: PresentationBlock | None = None,
    runs_if: str | None = None,
) -> StageDefinition:
    return StageDefinition(
        id=id,
        worker=worker,  # type: ignore[arg-type]
        agent_ref="x",
        inputs=InputSpec(required=inputs or [], optional=optional_inputs),
        outputs=OutputSpec(required=outputs or []),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(),
        loop_back=loop_back,
        parallel_with=parallel_with,
        presentation=presentation,
        runs_if=runs_if,
    )


# ---------------------------------------------------------------------------
# unique ids
# ---------------------------------------------------------------------------


def test_unique_ids_pass() -> None:
    assert validate_unique_ids([_stage("a"), _stage("b")]) == []


def test_unique_ids_duplicate() -> None:
    fs = validate_unique_ids([_stage("a"), _stage("a")])
    assert len(fs) == 1
    assert fs[0].rule == "unique_ids"
    assert fs[0].stage_id == "a"


# ---------------------------------------------------------------------------
# DAG closure
# ---------------------------------------------------------------------------


def test_dag_closure_first_stage_can_use_prompt_md() -> None:
    assert "prompt.md" in JOB_LEVEL_INPUTS
    assert validate_dag_closure([_stage("write", inputs=["prompt.md"], outputs=["spec.md"])]) == []


def test_dag_closure_chains_through_outputs() -> None:
    plan = [
        _stage("a", inputs=["prompt.md"], outputs=["spec.md"]),
        _stage("b", inputs=["spec.md"], outputs=["impl.md"]),
        _stage("c", inputs=["spec.md", "impl.md"], outputs=["pr.json"]),
    ]
    assert validate_dag_closure(plan) == []


def test_dag_closure_forward_reference_fails() -> None:
    plan = [
        _stage("a", inputs=["produced-by-b.md"], outputs=["spec.md"]),
        _stage("b", inputs=["spec.md"], outputs=["produced-by-b.md"]),
    ]
    fs = validate_dag_closure(plan)
    assert len(fs) == 1
    assert fs[0].stage_id == "a"
    assert "not produced by any prior stage" in fs[0].message


def test_dag_closure_optional_inputs_unchecked() -> None:
    plan = [
        _stage(
            "a",
            inputs=["prompt.md"],
            optional_inputs=["never-produced.json"],
            outputs=["x.md"],
        ),
    ]
    assert validate_dag_closure(plan) == []


# ---------------------------------------------------------------------------
# loop_back
# ---------------------------------------------------------------------------


def _loop_back(to: str, condition: str = "x.json.v == 'a'", max_iter: int = 3) -> LoopBack:
    return LoopBack(
        to=to,
        condition=condition,
        max_iterations=max_iter,
        on_exhaustion=OnExhaustion(kind="hil-manual-step", prompt="x"),
    )


def test_loop_back_to_earlier_stage_passes() -> None:
    plan = [
        _stage("write", outputs=["spec.md"]),
        _stage("review", inputs=["spec.md"], outputs=["v.json"], loop_back=_loop_back("write")),
    ]
    assert validate_loop_back_targets(plan) == []


def test_loop_back_to_self_fails() -> None:
    plan = [_stage("review", outputs=["v.json"], loop_back=_loop_back("review"))]
    fs = validate_loop_back_targets(plan)
    assert len(fs) == 1
    assert "loop-back stage itself" in fs[0].message


def test_loop_back_forward_reference_fails() -> None:
    plan = [
        _stage("a", outputs=["x.md"], loop_back=_loop_back("b")),
        _stage("b", outputs=["y.md"]),
    ]
    fs = validate_loop_back_targets(plan)
    assert len(fs) == 1
    assert "not an earlier stage" in fs[0].message


# ---------------------------------------------------------------------------
# parallel_with
# ---------------------------------------------------------------------------


def test_parallel_with_symmetric_passes() -> None:
    plan = [
        _stage("a", parallel_with=["b"]),
        _stage("b", parallel_with=["a"]),
    ]
    assert validate_parallel_with(plan) == []


def test_parallel_with_asymmetric_fails() -> None:
    plan = [
        _stage("a", parallel_with=["b"]),
        _stage("b"),  # no back-reference
    ]
    fs = validate_parallel_with(plan)
    assert len(fs) == 1
    assert "asymmetric" in fs[0].message


def test_parallel_with_unknown_id_fails() -> None:
    plan = [_stage("a", parallel_with=["does-not-exist"])]
    fs = validate_parallel_with(plan)
    assert any("unknown stage id" in f.message for f in fs)


# ---------------------------------------------------------------------------
# predicates
# ---------------------------------------------------------------------------


def test_runs_if_parses() -> None:
    assert validate_predicates([_stage("a", runs_if="some.json.verdict != 'approved'")]) == []


def test_runs_if_invalid_fails() -> None:
    fs = validate_predicates([_stage("a", runs_if="this is not valid syntax")])
    assert len(fs) >= 1


def test_loop_back_condition_invalid_fails() -> None:
    plan = [
        _stage("a", outputs=["v.json"]),
        _stage(
            "b",
            outputs=["w.json"],
            loop_back=_loop_back("a", condition="$$broken"),
        ),
    ]
    fs = validate_predicates(plan)
    assert any("loop_back.condition" in f.message for f in fs)


# ---------------------------------------------------------------------------
# human stages need presentation
# ---------------------------------------------------------------------------


def test_human_stage_without_presentation_fails() -> None:
    fs = validate_human_stages_have_presentation([_stage("h", worker="human")])
    assert len(fs) == 1
    assert "presentation" in fs[0].message


def test_human_stage_with_presentation_passes() -> None:
    fs = validate_human_stages_have_presentation(
        [_stage("h", worker="human", presentation=PresentationBlock(ui_template="x"))]
    )
    assert fs == []


def test_agent_stage_no_presentation_required() -> None:
    assert validate_human_stages_have_presentation([_stage("a", worker="agent")]) == []


# ---------------------------------------------------------------------------
# path traversal
# ---------------------------------------------------------------------------


def test_path_traversal_dotdot_fails() -> None:
    fs = validate_no_path_traversal([_stage("a", inputs=["../escape.md"])])
    assert len(fs) == 1
    assert "unsafe" in fs[0].message


def test_absolute_path_fails() -> None:
    fs = validate_no_path_traversal([_stage("a", inputs=["/etc/passwd"])])
    assert len(fs) == 1


def test_nested_relative_path_passes() -> None:
    fs = validate_no_path_traversal(
        [_stage("a", inputs=["sub/dir/file.md"], outputs=["out/file.json"])]
    )
    assert fs == []


# ---------------------------------------------------------------------------
# validate_known_validators (A2)
# ---------------------------------------------------------------------------


def test_validate_known_validators_passes_for_stage_with_no_validators() -> None:
    assert validate_known_validators([_stage("s1")]) == []


def test_validate_known_validators_passes_for_known_name() -> None:
    s = StageDefinition(
        id="s1",
        worker="agent",  # type: ignore[arg-type]
        agent_ref="x",
        inputs=InputSpec(),
        outputs=OutputSpec(),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(
            required_outputs=[RequiredOutput(path="out.txt", validators=["non-empty"])]
        ),
    )
    assert validate_known_validators([s]) == []


def test_validate_known_validators_rejects_unknown_name() -> None:
    s = StageDefinition(
        id="s1",
        worker="agent",  # type: ignore[arg-type]
        agent_ref="x",
        inputs=InputSpec(),
        outputs=OutputSpec(),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(
            required_outputs=[RequiredOutput(path="out.txt", validators=["totally-bogus"])]
        ),
    )
    failures = validate_known_validators([s])
    assert len(failures) == 1
    assert failures[0].rule == "known_validators"
    assert failures[0].stage_id == "s1"
    assert "totally-bogus" in failures[0].message


def test_validate_known_validators_rejects_unknown_artifact_validator_schema() -> None:
    s = StageDefinition(
        id="review",
        worker="agent",  # type: ignore[arg-type]
        agent_ref="x",
        inputs=InputSpec(),
        outputs=OutputSpec(),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(
            artifact_validators=[
                ArtifactValidator(**{"path": "review.json", "schema": "no-such-schema"})
            ]
        ),
    )
    failures = validate_known_validators([s])
    assert len(failures) == 1
    assert "no-such-schema" in failures[0].message


def test_validate_known_validators_passes_review_verdict_schema() -> None:
    s = StageDefinition(
        id="review",
        worker="agent",  # type: ignore[arg-type]
        agent_ref="x",
        inputs=InputSpec(),
        outputs=OutputSpec(),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(
            artifact_validators=[
                ArtifactValidator(**{"path": "review.json", "schema": "review-verdict-schema"})
            ]
        ),
    )
    assert validate_known_validators([s]) == []


def test_validate_plan_includes_known_validators_check() -> None:
    s = StageDefinition(
        id="s1",
        worker="agent",  # type: ignore[arg-type]
        agent_ref="x",
        inputs=InputSpec(),
        outputs=OutputSpec(),
        budget=Budget(max_turns=10),
        exit_condition=ExitCondition(
            required_outputs=[RequiredOutput(path="out.txt", validators=["ghost-validator"])]
        ),
    )
    failures = validate_plan([s])
    assert any(f.rule == "known_validators" for f in failures)
