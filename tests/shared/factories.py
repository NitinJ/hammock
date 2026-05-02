"""Lightweight model factories for tests.

These are plain helpers, not factory-boy classes — Pydantic v2 with
``ConfigDict(extra="forbid")`` is restrictive enough that bespoke factories
add value mostly as named "minimum-valid instance" constructors.
"""

from __future__ import annotations

from datetime import UTC, datetime

from shared.models import (
    AgentDef,
    AskAnswer,
    AskQuestion,
    Budget,
    Event,
    ExitCondition,
    HilItem,
    InputSpec,
    JobConfig,
    JobState,
    ManualStepQuestion,
    OutputSpec,
    PresentationBlock,
    Project,
    ReviewConcern,
    ReviewQuestion,
    ReviewVerdict,
    SkillDef,
    SpecialistCatalogue,
    StageDefinition,
    StageRun,
    StageState,
    TaskRecord,
    TaskState,
    UiTemplate,
)

_NOW = datetime(2026, 5, 2, tzinfo=UTC)


def make_project() -> Project:
    return Project(
        slug="figur-backend-v2",
        name="figur-backend-v2",
        repo_path="/home/nitin/workspace/figur-backend-v2",
        remote_url="https://github.com/yitfit/figur-backend-v2",
        default_branch="main",
        created_at=_NOW,
    )


def make_job() -> JobConfig:
    return JobConfig(
        job_id="job-001",
        job_slug="fix-login-bug-2026-05-02",
        project_slug="figur-backend-v2",
        job_type="fix-bug",
        created_at=_NOW,
        created_by="nitin",
        state=JobState.SUBMITTED,
    )


def make_budget() -> Budget:
    return Budget(max_turns=50, max_budget_usd=10.0, max_wall_clock_min=60)


def make_exit_condition() -> ExitCondition:
    return ExitCondition()


def make_stage_definition() -> StageDefinition:
    return StageDefinition(
        id="design",
        worker="agent",
        agent_ref="design-spec-writer",
        inputs=InputSpec(required=["prompt.md"]),
        outputs=OutputSpec(required=["design-spec.md"]),
        budget=make_budget(),
        exit_condition=make_exit_condition(),
    )


def make_stage_run() -> StageRun:
    return StageRun(stage_id="design", attempt=1, state=StageState.RUNNING)


def make_task_record() -> TaskRecord:
    return TaskRecord(
        task_id="task-1",
        stage_id="design",
        state=TaskState.RUNNING,
        created_at=_NOW,
    )


def make_ask_hil_item() -> HilItem:
    return HilItem(
        id="hil-001",
        kind="ask",
        stage_id="design",
        created_at=_NOW,
        status="awaiting",
        question=AskQuestion(text="Argon2id for password hashing?"),
    )


def make_review_hil_item() -> HilItem:
    return HilItem(
        id="hil-002",
        kind="review",
        stage_id="design-spec-review-human",
        created_at=_NOW,
        status="awaiting",
        question=ReviewQuestion(target="design-spec.md", prompt="Approve or reject."),
    )


def make_manual_step_hil_item() -> HilItem:
    return HilItem(
        id="hil-003",
        kind="manual-step",
        stage_id="loop-exhaustion",
        created_at=_NOW,
        status="awaiting",
        question=ManualStepQuestion(instructions="Manual fix required."),
    )


def make_event() -> Event:
    return Event(
        seq=1,
        timestamp=_NOW,
        event_type="stage_state_transition",
        source="job_driver",
        job_id="job-001",
        stage_id="design",
        payload={"from": "READY", "to": "RUNNING"},
    )


def make_review_verdict() -> ReviewVerdict:
    return ReviewVerdict(
        verdict="approved",
        summary="LGTM",
        unresolved_concerns=[
            ReviewConcern(severity="minor", concern="naming nit", location="general"),
        ],
        addressed_in_this_iteration=[],
    )


def make_presentation_block() -> PresentationBlock:
    return PresentationBlock(ui_template="design-spec-review-form")


def make_ui_template() -> UiTemplate:
    return UiTemplate(name="design-spec-review-form", description="Review the design spec")


def make_agent_def() -> AgentDef:
    return AgentDef(
        agent_ref="design-spec-writer",
        name="Design-spec writer",
        description="Writes design specs",
        model="claude-opus-4-7",
        body="You are a design-spec writer.",
    )


def make_skill_def() -> SkillDef:
    return SkillDef(
        skill_id="markdown-spec",
        description="Writes well-structured markdown specs",
        triggering_summary="When the user asks to draft a design spec",
    )


def make_specialist_catalogue() -> SpecialistCatalogue:
    return SpecialistCatalogue(project_slug="figur-backend-v2")


def make_ask_answer() -> AskAnswer:
    return AskAnswer(text="Yes, use Argon2id.")
