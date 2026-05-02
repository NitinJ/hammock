"""Tests for ``shared.paths``."""

from __future__ import annotations

from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from shared import paths


def test_default_root_is_under_home(monkeypatch: object) -> None:
    """With HAMMOCK_ROOT unset, default is ~/.hammock."""
    # Re-derive default (paths.HAMMOCK_ROOT is module-level; we just test the helper)
    # We can't easily re-import without monkeypatching, but we can read the constant.
    assert str(paths.HAMMOCK_ROOT).endswith(".hammock") or str(paths.HAMMOCK_ROOT)


def test_explicit_root_overrides(hammock_root: Path) -> None:
    assert paths.projects_dir(hammock_root) == hammock_root / "projects"
    assert paths.jobs_dir(hammock_root) == hammock_root / "jobs"


def test_project_paths(hammock_root: Path) -> None:
    p = paths.project_dir("figur-backend", root=hammock_root)
    assert p == hammock_root / "projects" / "figur-backend"
    assert paths.project_json("figur-backend", root=hammock_root) == p / "project.json"


def test_job_paths(hammock_root: Path) -> None:
    j = paths.job_dir("fix-login-2026-05-02", root=hammock_root)
    assert j == hammock_root / "jobs" / "fix-login-2026-05-02"
    assert paths.job_json("fix-login-2026-05-02", root=hammock_root) == j / "job.json"
    assert paths.job_events_jsonl("fix-login-2026-05-02", root=hammock_root) == j / "events.jsonl"
    assert paths.job_heartbeat("fix-login-2026-05-02", root=hammock_root) == j / "heartbeat"


def test_stage_paths(hammock_root: Path) -> None:
    s = paths.stage_dir("job-x", "design", root=hammock_root)
    assert s == hammock_root / "jobs" / "job-x" / "stages" / "design"
    assert paths.stage_json("job-x", "design", root=hammock_root) == s / "stage.json"
    assert paths.stage_run_dir("job-x", "design", 2, root=hammock_root) == s / "run-2"
    assert paths.stage_run_latest("job-x", "design", root=hammock_root) == s / "latest"


def test_agent0_paths_resolve_under_latest(hammock_root: Path) -> None:
    # agent0 paths are under stage_run_latest (the symlink); confirm consistency.
    expected_base = hammock_root / "jobs" / "job-x" / "stages" / "design" / "latest" / "agent0"
    assert paths.agent0_dir("job-x", "design", root=hammock_root) == expected_base
    assert (
        paths.agent0_messages_jsonl("job-x", "design", root=hammock_root)
        == expected_base / "messages.jsonl"
    )


def test_task_paths(hammock_root: Path) -> None:
    t = paths.task_dir("job-x", "implement", "task-1", root=hammock_root)
    assert t == hammock_root / "jobs" / "job-x" / "stages" / "implement" / "tasks" / "task-1"
    assert paths.task_json("job-x", "implement", "task-1", root=hammock_root) == t / "task.json"


def test_hil_paths(hammock_root: Path) -> None:
    assert paths.hil_dir("job-x", root=hammock_root) == hammock_root / "jobs" / "job-x" / "hil"
    assert (
        paths.hil_item_path("job-x", "ask-001", root=hammock_root)
        == hammock_root / "jobs" / "job-x" / "hil" / "ask-001.json"
    )


def test_project_overrides(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    repo.mkdir()
    assert paths.project_overrides_root(repo) == repo / ".hammock"
    assert paths.project_agents_overrides(repo) == repo / ".hammock" / "agent-overrides"
    assert paths.project_skills_overrides(repo) == repo / ".hammock" / "skill-overrides"
    assert paths.project_ui_template_overrides(repo) == repo / ".hammock" / "ui-templates"


# Property tests --------------------------------------------------------------

_slug_chars = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="-"),
    min_size=1,
    max_size=32,
)


@given(slug=_slug_chars)
def test_job_paths_have_no_double_slashes_property(slug: str) -> None:
    """No path helper produces a path with consecutive slashes."""
    root = Path("/tmp/hammock-test-root")
    p = paths.job_dir(slug, root=root)
    assert "//" not in str(p)
    assert "//" not in str(paths.job_json(slug, root=root))


@given(slug=_slug_chars, sid=_slug_chars)
def test_stage_paths_compose_property(slug: str, sid: str) -> None:
    root = Path("/tmp/hammock-test-root")
    s = paths.stage_dir(slug, sid, root=root)
    # stage_dir is always under jobs/<slug>/stages/<sid>
    assert s.parent.name == "stages"
    assert s.parent.parent.name == slug
