"""Tests for ``job_driver.prompt_builder.build_stage_prompt``.

Per real-claude e2e precondition track P2: the runner must hand the
agent a structured prompt with the job context, declared inputs +
outputs, and the working directory — not a one-liner stage description.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from job_driver.prompt_builder import build_stage_prompt
from shared.models.stage import (
    ArtifactValidator,
    Budget,
    ExitCondition,
    InputSpec,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
)


def _stage(
    *,
    sid: str = "fix-issue",
    description: str | None = "Fix the failing test",
    inputs_required: list[str] | None = None,
    inputs_optional: list[str] | None = None,
    outputs: list[RequiredOutput] | None = None,
    validators: list[ArtifactValidator] | None = None,
) -> StageDefinition:
    return StageDefinition(
        id=sid,
        description=description,
        worker="agent",
        agent_ref=None,
        inputs=InputSpec(required=inputs_required or [], optional=inputs_optional),
        outputs=OutputSpec(required=[]),
        budget=Budget(max_budget_usd=1.0),
        exit_condition=ExitCondition(
            required_outputs=outputs,
            artifact_validators=validators,
        ),
    )


def test_includes_stage_description(tmp_path: Path) -> None:
    prompt = build_stage_prompt(
        _stage(description="Find the bug"),
        job_prompt="ignored",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    assert "Find the bug" in prompt


def test_includes_stage_id_when_no_description(tmp_path: Path) -> None:
    prompt = build_stage_prompt(
        _stage(sid="lonely-stage", description=None),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    assert "lonely-stage" in prompt


def test_includes_job_prompt_section(tmp_path: Path) -> None:
    prompt = build_stage_prompt(
        _stage(),
        job_prompt="Make the dashboard render PRs.",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    assert "## Job context" in prompt
    assert "Make the dashboard render PRs." in prompt


def test_lists_required_outputs_with_schemas(tmp_path: Path) -> None:
    prompt = build_stage_prompt(
        _stage(
            outputs=[
                RequiredOutput(path="problem-spec.md"),
                RequiredOutput(path="impl-plan.md"),
            ],
            validators=[
                ArtifactValidator(path="problem-spec.md", schema="non-empty"),
                ArtifactValidator(path="impl-plan.md", schema="plan-schema"),
            ],
        ),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    assert "## Required outputs" in prompt
    assert "problem-spec.md" in prompt
    assert "non-empty" in prompt
    assert "impl-plan.md" in prompt
    assert "plan-schema" in prompt


def test_lists_required_outputs_without_validators(tmp_path: Path) -> None:
    """When a required output has no registered validator, the prompt
    still names the path so the agent knows it's required."""
    prompt = build_stage_prompt(
        _stage(outputs=[RequiredOutput(path="summary.md")], validators=None),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    assert "summary.md" in prompt


def test_inlines_existing_required_input_excerpt(tmp_path: Path) -> None:
    (tmp_path / "spec.md").write_text("# Spec\nDo the thing.\n")
    prompt = build_stage_prompt(
        _stage(inputs_required=["spec.md"]),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    assert "## Required inputs" in prompt
    assert "spec.md" in prompt
    assert "Do the thing." in prompt


def test_flags_missing_required_input(tmp_path: Path) -> None:
    prompt = build_stage_prompt(
        _stage(inputs_required=["nope.md"]),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    assert "nope.md" in prompt
    assert "not found" in prompt.lower()


def test_truncates_long_inputs(tmp_path: Path) -> None:
    big = "X" * 50_000
    (tmp_path / "big.md").write_text(big)
    prompt = build_stage_prompt(
        _stage(inputs_required=["big.md"]),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
        max_input_bytes=1024,
    )
    assert "[truncated]" in prompt
    # The full 50k must not be inlined.
    assert prompt.count("X") < 50_000


def test_includes_cwd_section(tmp_path: Path) -> None:
    cwd = tmp_path / "worktree"
    cwd.mkdir()
    prompt = build_stage_prompt(
        _stage(),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=cwd,
    )
    assert "## Working directory" in prompt
    assert str(cwd) in prompt


def test_pure_function_signature() -> None:
    """Builder must not accept positional kwargs implicitly — guards
    against silent drift if call sites change argument order."""
    import inspect

    sig = inspect.signature(build_stage_prompt)
    params = list(sig.parameters.values())
    # First positional arg is stage_def; rest are keyword-only.
    assert params[0].kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
    for p in params[1:]:
        assert p.kind == inspect.Parameter.KEYWORD_ONLY, p.name


def test_optional_inputs_are_listed_separately(tmp_path: Path) -> None:
    (tmp_path / "opt.md").write_text("optional content")
    prompt = build_stage_prompt(
        _stage(inputs_optional=["opt.md"]),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    # Both required and optional get inlined; the section heading
    # makes the distinction so the agent knows what's optional.
    assert "## Optional inputs" in prompt or "optional" in prompt.lower()
    assert "opt.md" in prompt


def test_no_inputs_section_when_none_declared(tmp_path: Path) -> None:
    """If a stage declares neither required nor optional inputs, the
    prompt should not include an empty 'Required inputs' heading."""
    prompt = build_stage_prompt(
        _stage(),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    assert "## Required inputs" not in prompt


def test_no_outputs_section_when_none_declared(tmp_path: Path) -> None:
    prompt = build_stage_prompt(
        _stage(outputs=None, validators=None),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    assert "## Required outputs" not in prompt


def test_returns_str() -> None:
    """Sanity: the builder returns a non-empty str."""
    result = build_stage_prompt(
        _stage(),
        job_prompt="x",
        job_dir=Path("/tmp"),
        cwd=Path("/tmp"),
    )
    assert isinstance(result, str)
    assert result.strip()


# -- Snapshot test --------------------------------------------------------


def test_snapshot_pin_for_canonical_stage(tmp_path: Path) -> None:
    """Lock the rendered prompt's stable section headings + ordering.

    Loosened from byte-for-byte snapshot to structural assertions in
    PR #29 dogfood: the byte-snapshot churned every time we tightened
    the agent-facing wording (e.g. naming the JOB DIR explicitly so
    claude doesn't write outputs into the worktree).
    """
    (tmp_path / "spec.md").write_text("# Spec\n\nAdd SSE.")
    cwd = tmp_path / "wt"
    cwd.mkdir()
    stage = _stage(
        sid="write-impl-plan",
        description="Decompose the task into actionable steps.",
        inputs_required=["spec.md"],
        outputs=[RequiredOutput(path="impl-plan.md")],
        validators=[ArtifactValidator(path="impl-plan.md", schema="plan-schema")],
    )
    prompt = build_stage_prompt(
        stage,
        job_prompt="Add the SSE endpoint per the design doc.",
        job_dir=tmp_path,
        cwd=cwd,
    )
    # Stage header + description.
    assert "# Stage: write-impl-plan" in prompt
    assert "Decompose the task into actionable steps." in prompt
    # Job context section + content.
    assert "## Job context" in prompt
    assert "Add the SSE endpoint per the design doc." in prompt
    # Inputs section + inlined file content.
    assert "## Required inputs" in prompt
    assert "### spec.md" in prompt
    assert "Add SSE." in prompt
    # Outputs section: name, schema, AND absolute job_dir path.
    assert "## Required outputs" in prompt
    assert "impl-plan.md" in prompt
    assert "plan-schema" in prompt
    assert str(tmp_path) in prompt  # job_dir is named so claude knows where
    # Working directory section: cwd named, distinguished from job_dir.
    assert "## Working directory" in prompt
    assert str(cwd) in prompt
    # Section ordering — pin the agent's reading order.
    assert prompt.index("## Job context") < prompt.index("## Required inputs")
    assert prompt.index("## Required inputs") < prompt.index("## Required outputs")
    assert prompt.index("## Required outputs") < prompt.index("## Working directory")


# -- Pure-function check (no side effects) --------------------------------


def test_no_io_to_cwd(tmp_path: Path) -> None:
    """Builder must not write to cwd (it's the agent's worktree)."""
    cwd = tmp_path / "wt"
    cwd.mkdir()
    before = sorted(p.name for p in cwd.iterdir())
    build_stage_prompt(
        _stage(),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=cwd,
    )
    after = sorted(p.name for p in cwd.iterdir())
    assert before == after


def test_input_paths_resolve_against_job_dir(tmp_path: Path) -> None:
    """Input paths in stage_def.inputs are interpreted relative to
    job_dir, NOT cwd. Confirms because cwd contains a same-named file
    that the builder must NOT pick up."""
    (tmp_path / "input.md").write_text("from job dir")
    cwd = tmp_path / "wt"
    cwd.mkdir()
    (cwd / "input.md").write_text("from cwd — should not appear")
    prompt = build_stage_prompt(
        _stage(inputs_required=["input.md"]),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=cwd,
    )
    assert "from job dir" in prompt
    assert "from cwd" not in prompt


def test_input_outside_job_dir_is_rejected(tmp_path: Path) -> None:
    """A path that escapes job_dir (e.g. ``../etc/passwd``) must be
    flagged, not read. The agent gets a clear "outside job dir" notice."""
    prompt = build_stage_prompt(
        _stage(inputs_required=["../escape.md"]),
        job_prompt="x",
        job_dir=tmp_path / "jobs" / "j",
        cwd=tmp_path,
    )
    # Either flag explicitly or not found — but must NOT read disk.
    assert "escape.md" in prompt
    assert "outside" in prompt.lower() or "not found" in prompt.lower()


@pytest.mark.parametrize("description", ["", None, "   "])
def test_blank_description_falls_back_to_id(tmp_path: Path, description: str | None) -> None:
    prompt = build_stage_prompt(
        _stage(sid="fallback-id", description=description),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
    )
    assert "fallback-id" in prompt


# -- Codex-review follow-ups (PR #26) -------------------------------------


def test_aggregate_input_budget_omits_overflow(tmp_path: Path) -> None:
    """Per-file cap alone doesn't help when many inputs are declared.
    Total inlined bytes are bounded by ``max_total_input_bytes``; inputs
    past the cap are listed by name with an "omitted" notice."""
    (tmp_path / "a.md").write_text("A" * 1000)
    (tmp_path / "b.md").write_text("B" * 1000)
    (tmp_path / "c.md").write_text("C" * 1000)
    prompt = build_stage_prompt(
        _stage(inputs_required=["a.md", "b.md", "c.md"]),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
        max_input_bytes=2000,
        max_total_input_bytes=1500,
    )
    assert "c.md" in prompt
    assert "omitted" in prompt.lower()
    # The C payload (1000 chars) must NOT make it into the prompt.
    assert prompt.count("C") < 100


def test_aggregate_budget_shared_across_required_and_optional(tmp_path: Path) -> None:
    """Required + optional inputs share one aggregate budget; otherwise
    a long required input would still let optional inputs blow the cap."""
    (tmp_path / "req.md").write_text("R" * 1500)
    (tmp_path / "opt.md").write_text("O" * 1500)
    prompt = build_stage_prompt(
        _stage(inputs_required=["req.md"], inputs_optional=["opt.md"]),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
        max_input_bytes=2000,
        max_total_input_bytes=1500,
    )
    assert "omitted" in prompt.lower()
    assert prompt.count("O") < 100  # 'O' header letter from "Optional" allowed


def test_does_not_load_full_file_above_cap(tmp_path: Path) -> None:
    """Builder must not slurp arbitrarily large files before truncating."""
    big_file = tmp_path / "big.md"
    big_file.write_bytes(b"X" * (10 * 1024 * 1024))  # 10 MB
    prompt = build_stage_prompt(
        _stage(inputs_required=["big.md"]),
        job_prompt="x",
        job_dir=tmp_path,
        cwd=tmp_path,
        max_input_bytes=4096,
    )
    assert prompt.count("X") <= 4096
    assert "[truncated]" in prompt
