"""Unit tests for shared/v1/paths.py."""

from __future__ import annotations

from pathlib import Path

from shared.v1 import paths


def test_job_dir(tmp_path: Path) -> None:
    assert paths.job_dir("j1", root=tmp_path) == tmp_path / "jobs" / "j1"


def test_job_config_path(tmp_path: Path) -> None:
    assert paths.job_config_path("j1", root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "job.json"
    )


def test_variable_envelope_path(tmp_path: Path) -> None:
    assert paths.variable_envelope_path("j1", "bug_report", root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "variables" / "bug_report.json"
    )


def test_node_state_path(tmp_path: Path) -> None:
    assert paths.node_state_path("j1", "write-bug-report", root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "nodes" / "write-bug-report" / "state.json"
    )


def test_node_attempt_dir(tmp_path: Path) -> None:
    assert paths.node_attempt_dir("j1", "n", 2, root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "nodes" / "n" / "runs" / "2"
    )


def test_ensure_job_layout_creates_skeleton(tmp_path: Path) -> None:
    paths.ensure_job_layout("j1", root=tmp_path)
    assert (tmp_path / "jobs" / "j1").is_dir()
    assert (tmp_path / "jobs" / "j1" / "variables").is_dir()
    assert (tmp_path / "jobs" / "j1" / "nodes").is_dir()


def test_ensure_job_layout_idempotent(tmp_path: Path) -> None:
    paths.ensure_job_layout("j1", root=tmp_path)
    paths.ensure_job_layout("j1", root=tmp_path)  # second call must not raise
