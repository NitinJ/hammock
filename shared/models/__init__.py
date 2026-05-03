"""Pydantic models — the cross-process contract surface.

Imported by both ``dashboard/`` and ``job_driver/``. Every persisted file in
the hammock root has a model here; every HTTP response shape is derived from
one.

Design doc canonical references:
- § Project Registry              → project.py
- § Lifecycle § Job state machine → job.py
- § Stage as universal primitive  → stage.py
- § Lifecycle § Task state mach.  → task.py
- § HIL bridge § HIL typed shapes → hil.py
- § Observability § Event stream  → events.py
- § Plan Compiler                 → plan.py, verdict.py
- § Presentation plane            → presentation.py
- § Job templates, agents, skills → specialist.py
"""

from shared.models.events import Event
from shared.models.hil import (
    AskAnswer,
    AskQuestion,
    HilItem,
    ManualStepAnswer,
    ManualStepQuestion,
    ReviewAnswer,
    ReviewQuestion,
)
from shared.models.integration_test_report import IntegrationTestReport, TestFailure
from shared.models.job import (
    AgentCostSummary,
    JobConfig,
    JobCostSummary,
    JobState,
    StageCostSummary,
)
from shared.models.plan import Plan, PlanStage
from shared.models.presentation import PresentationBlock, UiTemplate
from shared.models.project import Project, ProjectConfig
from shared.models.specialist import (
    AgentDef,
    AgentEntry,
    MaterialisedSpawn,
    SkillDef,
    SkillEntry,
    SpecialistCatalogue,
)
from shared.models.stage import (
    ArtifactValidator,
    Budget,
    ExitCondition,
    InputSpec,
    LoopBack,
    OnExhaustion,
    OutputSpec,
    RequiredOutput,
    StageDefinition,
    StageRun,
    StageState,
)
from shared.models.task import TaskRecord, TaskState
from shared.models.verdict import ReviewConcern, ReviewVerdict

__all__ = [
    "AgentCostSummary",
    "AgentDef",
    "AgentEntry",
    "ArtifactValidator",
    "AskAnswer",
    "AskQuestion",
    "Budget",
    "Event",
    "ExitCondition",
    "HilItem",
    "InputSpec",
    "IntegrationTestReport",
    "JobConfig",
    "JobCostSummary",
    "JobState",
    "LoopBack",
    "ManualStepAnswer",
    "ManualStepQuestion",
    "MaterialisedSpawn",
    "OnExhaustion",
    "OutputSpec",
    "Plan",
    "PlanStage",
    "PresentationBlock",
    "Project",
    "ProjectConfig",
    "RequiredOutput",
    "ReviewAnswer",
    "ReviewConcern",
    "ReviewQuestion",
    "ReviewVerdict",
    "SkillDef",
    "SkillEntry",
    "SpecialistCatalogue",
    "StageCostSummary",
    "StageDefinition",
    "StageRun",
    "StageState",
    "TaskRecord",
    "TaskState",
    "TestFailure",
    "UiTemplate",
]
