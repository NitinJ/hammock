"""Canonical path layout for the hammock root.

Every path that hammock writes to or reads from MUST be produced by a
function in this module. Hardcoded paths anywhere else in the codebase are
a CI failure (enforced by an import-linter rule once Stage 8 lands).

The default root is ``~/.hammock/``. For tests, override the root by passing
an explicit ``root`` argument; production code uses the module-level
``HAMMOCK_ROOT`` derived from the ``HAMMOCK_ROOT`` environment variable
or ``~/.hammock`` if unset.
"""

from __future__ import annotations

import os
from pathlib import Path


def _default_root() -> Path:
    env = os.environ.get("HAMMOCK_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".hammock"


HAMMOCK_ROOT: Path = _default_root()


# ---------------------------------------------------------------------------
# Top-level dirs
# ---------------------------------------------------------------------------


def hammock_root(root: Path | None = None) -> Path:
    return root if root is not None else HAMMOCK_ROOT


def projects_dir(root: Path | None = None) -> Path:
    return hammock_root(root) / "projects"


def jobs_dir(root: Path | None = None) -> Path:
    return hammock_root(root) / "jobs"


def agents_dir(root: Path | None = None) -> Path:
    return hammock_root(root) / "agents"


def skills_dir(root: Path | None = None) -> Path:
    return hammock_root(root) / "skills"


def ui_templates_dir(root: Path | None = None) -> Path:
    return hammock_root(root) / "ui-templates"


def hooks_dir(root: Path | None = None) -> Path:
    return hammock_root(root) / "hooks"


def observatory_dir(root: Path | None = None) -> Path:
    return hammock_root(root) / "observatory"


def job_templates_dir(root: Path | None = None) -> Path:
    return hammock_root(root) / "job-templates"


# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------


def project_dir(slug: str, *, root: Path | None = None) -> Path:
    return projects_dir(root) / slug


def project_json(slug: str, *, root: Path | None = None) -> Path:
    return project_dir(slug, root=root) / "project.json"


# ---------------------------------------------------------------------------
# Job paths
# ---------------------------------------------------------------------------


def job_dir(job_slug: str, *, root: Path | None = None) -> Path:
    return jobs_dir(root) / job_slug


def job_json(job_slug: str, *, root: Path | None = None) -> Path:
    return job_dir(job_slug, root=root) / "job.json"


def job_driver_pid(job_slug: str, *, root: Path | None = None) -> Path:
    return job_dir(job_slug, root=root) / "job-driver.pid"


def job_heartbeat(job_slug: str, *, root: Path | None = None) -> Path:
    return job_dir(job_slug, root=root) / "heartbeat"


def job_human_action(job_slug: str, *, root: Path | None = None) -> Path:
    return job_dir(job_slug, root=root) / "human-action.json"


def job_events_jsonl(job_slug: str, *, root: Path | None = None) -> Path:
    return job_dir(job_slug, root=root) / "events.jsonl"


def job_driver_log(job_slug: str, *, root: Path | None = None) -> Path:
    return job_dir(job_slug, root=root) / "job-driver.log"


def job_stage_list(job_slug: str, *, root: Path | None = None) -> Path:
    return job_dir(job_slug, root=root) / "stage-list.yaml"


def job_prompt(job_slug: str, *, root: Path | None = None) -> Path:
    return job_dir(job_slug, root=root) / "prompt.md"


def job_artifact(job_slug: str, name: str, *, root: Path | None = None) -> Path:
    """Job-level artifact (problem-spec.md, design-spec.md, plan.yaml, ...)."""
    return job_dir(job_slug, root=root) / name


# ---------------------------------------------------------------------------
# Stage paths
# ---------------------------------------------------------------------------


def stages_dir(job_slug: str, *, root: Path | None = None) -> Path:
    return job_dir(job_slug, root=root) / "stages"


def stage_dir(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    return stages_dir(job_slug, root=root) / stage_id


def stage_json(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    return stage_dir(job_slug, stage_id, root=root) / "stage.json"


def stage_events_jsonl(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    return stage_dir(job_slug, stage_id, root=root) / "events.jsonl"


def stage_orchestrator_log(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    return stage_dir(job_slug, stage_id, root=root) / "orchestrator-session.log"


def stage_nudges_jsonl(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    return stage_dir(job_slug, stage_id, root=root) / "nudges.jsonl"


def stage_pr_info(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    return stage_dir(job_slug, stage_id, root=root) / "pr-info.json"


# Per-run subpaths (StageRun storage: stages/<id>/run-N/)
def stage_run_dir(job_slug: str, stage_id: str, run_n: int, *, root: Path | None = None) -> Path:
    return stage_dir(job_slug, stage_id, root=root) / f"run-{run_n}"


def stage_run_latest(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    """Symlink to the latest run dir."""
    return stage_dir(job_slug, stage_id, root=root) / "latest"


# Agent0 stream files (under stage_run, conventionally "latest")
def agent0_dir(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    return stage_run_latest(job_slug, stage_id, root=root) / "agent0"


def agent0_messages_jsonl(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    return agent0_dir(job_slug, stage_id, root=root) / "messages.jsonl"


def agent0_tool_uses_jsonl(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    return agent0_dir(job_slug, stage_id, root=root) / "tool-uses.jsonl"


def agent0_subagent_dir(
    job_slug: str, stage_id: str, subagent_id: str, *, root: Path | None = None
) -> Path:
    return agent0_dir(job_slug, stage_id, root=root) / "subagents" / subagent_id


# ---------------------------------------------------------------------------
# Task paths
# ---------------------------------------------------------------------------


def tasks_dir(job_slug: str, stage_id: str, *, root: Path | None = None) -> Path:
    return stage_dir(job_slug, stage_id, root=root) / "tasks"


def task_dir(job_slug: str, stage_id: str, task_id: str, *, root: Path | None = None) -> Path:
    return tasks_dir(job_slug, stage_id, root=root) / task_id


def task_json(job_slug: str, stage_id: str, task_id: str, *, root: Path | None = None) -> Path:
    return task_dir(job_slug, stage_id, task_id, root=root) / "task.json"


def task_events_jsonl(
    job_slug: str, stage_id: str, task_id: str, *, root: Path | None = None
) -> Path:
    return task_dir(job_slug, stage_id, task_id, root=root) / "events.jsonl"


def task_spec(job_slug: str, stage_id: str, task_id: str, *, root: Path | None = None) -> Path:
    return task_dir(job_slug, stage_id, task_id, root=root) / "task-spec.md"


def task_work_dir(job_slug: str, stage_id: str, task_id: str, *, root: Path | None = None) -> Path:
    return task_dir(job_slug, stage_id, task_id, root=root) / "work"


# ---------------------------------------------------------------------------
# HIL paths
# ---------------------------------------------------------------------------


def hil_dir(job_slug: str, *, root: Path | None = None) -> Path:
    return job_dir(job_slug, root=root) / "hil"


def hil_item_path(job_slug: str, item_id: str, *, root: Path | None = None) -> Path:
    return hil_dir(job_slug, root=root) / f"{item_id}.json"


# ---------------------------------------------------------------------------
# Per-project override paths (under <repo>/.hammock/)
# ---------------------------------------------------------------------------


def project_overrides_root(repo_path: Path) -> Path:
    return repo_path / ".hammock"


def project_agents_overrides(repo_path: Path) -> Path:
    return project_overrides_root(repo_path) / "agent-overrides"


def project_skills_overrides(repo_path: Path) -> Path:
    return project_overrides_root(repo_path) / "skill-overrides"


def project_ui_template_overrides(repo_path: Path) -> Path:
    return project_overrides_root(repo_path) / "ui-templates"
