"""Unit tests for shared/v1/paths.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.v1 import paths

# ---------------------------------------------------------------------------
# iter_token / parse_iter_token
# ---------------------------------------------------------------------------


def test_iter_token_top() -> None:
    assert paths.iter_token(()) == "top"


def test_iter_token_single() -> None:
    assert paths.iter_token((0,)) == "i0"
    assert paths.iter_token((3,)) == "i3"


def test_iter_token_nested() -> None:
    assert paths.iter_token((0, 1)) == "i0_1"
    assert paths.iter_token((2, 0, 4)) == "i2_0_4"


def test_iter_token_rejects_negative() -> None:
    with pytest.raises(ValueError):
        paths.iter_token((-1,))
    with pytest.raises(ValueError):
        paths.iter_token((0, -1, 2))


def test_parse_iter_token_top() -> None:
    assert paths.parse_iter_token("top") == ()


def test_parse_iter_token_single_and_nested() -> None:
    assert paths.parse_iter_token("i0") == (0,)
    assert paths.parse_iter_token("i7") == (7,)
    assert paths.parse_iter_token("i0_1") == (0, 1)
    assert paths.parse_iter_token("i2_0_4") == (2, 0, 4)


@pytest.mark.parametrize("bad", ["", "0", "0_1", "ix", "i", "i0_x"])
def test_parse_iter_token_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        paths.parse_iter_token(bad)


def test_iter_token_round_trip_top() -> None:
    assert paths.parse_iter_token(paths.iter_token(())) == ()


def test_iter_token_round_trip_single() -> None:
    assert paths.parse_iter_token(paths.iter_token((4,))) == (4,)


def test_iter_token_round_trip_deeply_nested() -> None:
    deep = (0, 1, 2, 3, 4, 5, 6, 7)
    assert paths.parse_iter_token(paths.iter_token(deep)) == deep


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def test_job_dir(tmp_path: Path) -> None:
    assert paths.job_dir("j1", root=tmp_path) == tmp_path / "jobs" / "j1"


def test_job_config_path(tmp_path: Path) -> None:
    assert paths.job_config_path("j1", root=tmp_path) == (tmp_path / "jobs" / "j1" / "job.json")


def test_variable_envelope_path_top(tmp_path: Path) -> None:
    assert paths.variable_envelope_path("j1", "bug_report", root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "variables" / "bug_report__top.json"
    )


def test_variable_envelope_path_single_iter(tmp_path: Path) -> None:
    assert paths.variable_envelope_path("j1", "design_spec", (0,), root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "variables" / "design_spec__i0.json"
    )


def test_variable_envelope_path_nested(tmp_path: Path) -> None:
    assert paths.variable_envelope_path("j1", "design_spec", (1, 2), root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "variables" / "design_spec__i1_2.json"
    )


def test_node_state_path_top(tmp_path: Path) -> None:
    assert paths.node_state_path("j1", "write-bug-report", root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "nodes" / "write-bug-report" / "top" / "state.json"
    )


def test_node_state_path_loop_body(tmp_path: Path) -> None:
    assert paths.node_state_path("j1", "write-design", (0, 1), root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "nodes" / "write-design" / "i0_1" / "state.json"
    )


def test_node_attempt_dir_top(tmp_path: Path) -> None:
    assert paths.node_attempt_dir("j1", "n", 2, root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "nodes" / "n" / "top" / "runs" / "2"
    )


def test_node_attempt_dir_nested(tmp_path: Path) -> None:
    assert paths.node_attempt_dir("j1", "n", 1, (2, 3), root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "nodes" / "n" / "i2_3" / "runs" / "1"
    )


def test_node_iter_dir(tmp_path: Path) -> None:
    assert paths.node_iter_dir("j1", "n", (0,), root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "nodes" / "n" / "i0"
    )


def test_pending_marker_path_top(tmp_path: Path) -> None:
    assert paths.pending_marker_path("j1", "review-human", root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "pending" / "review-human__top.json"
    )


def test_pending_marker_path_nested(tmp_path: Path) -> None:
    assert paths.pending_marker_path("j1", "review-human", (0, 2), root=tmp_path) == (
        tmp_path / "jobs" / "j1" / "pending" / "review-human__i0_2.json"
    )


def test_ensure_job_layout_creates_skeleton(tmp_path: Path) -> None:
    paths.ensure_job_layout("j1", root=tmp_path)
    assert (tmp_path / "jobs" / "j1").is_dir()
    assert (tmp_path / "jobs" / "j1" / "variables").is_dir()
    assert (tmp_path / "jobs" / "j1" / "nodes").is_dir()


def test_ensure_job_layout_idempotent(tmp_path: Path) -> None:
    paths.ensure_job_layout("j1", root=tmp_path)
    paths.ensure_job_layout("j1", root=tmp_path)  # second call must not raise
