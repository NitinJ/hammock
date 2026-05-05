"""Unit tests for tests/e2e_v1/cleanup.py."""

from __future__ import annotations

import dataclasses
import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.e2e_v1.cleanup import RunSnapshot, take_snapshot, teardown


@dataclasses.dataclass
class _Call:
    args: list[str]


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[_Call] = []
        self._handlers: dict[
            tuple[str, ...],
            Callable[[list[str]], subprocess.CompletedProcess[str]],
        ] = {}

    def expect(
        self,
        prefix: tuple[str, ...],
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        def handler(args: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=args, returncode=returncode, stdout=stdout, stderr=stderr
            )

        self._handlers[prefix] = handler

    def __call__(self, args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(_Call(list(args)))
        best: tuple[str, ...] | None = None
        for prefix in self._handlers:
            if tuple(args[: len(prefix)]) == prefix:
                if best is None or len(prefix) > len(best):
                    best = prefix
        if best is None:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")
        return self._handlers[best](args)


def _gh_branch_listing(branches: list[str]) -> str:
    return "\n".join(branches)


# ---------------------------------------------------------------------------
# take_snapshot
# ---------------------------------------------------------------------------


def test_snapshot_records_pre_existing_branches() -> None:
    runner = FakeRunner()
    runner.expect(
        ("gh", "api"),
        stdout=_gh_branch_listing(["main", "feature-existing"]),
    )
    snap = take_snapshot("me/e2e-test", runner=runner)
    assert snap == RunSnapshot(pre_branches={"main", "feature-existing"})


def test_snapshot_returns_empty_set_on_failure() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api"), returncode=1, stderr="rate limit")
    snap = take_snapshot("me/e2e-test", runner=runner)
    assert snap.pre_branches == set()


# ---------------------------------------------------------------------------
# teardown — cost summary
# ---------------------------------------------------------------------------


def test_logs_cost_summary_when_present(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    job_dir = tmp_path / "root" / "jobs" / "j"
    job_dir.mkdir(parents=True)
    (job_dir / "cost_summary.json").write_text(json.dumps({"total_usd": 0.4216}))

    runner = FakeRunner()
    runner.expect(("gh", "api"), stdout=_gh_branch_listing([]))

    with caplog.at_level(logging.INFO, logger="tests.e2e_v1.cleanup"):
        teardown(
            root=tmp_path / "root",
            repo_slug="me/e2e-test",
            snapshot=RunSnapshot(pre_branches=set()),
            keep_root=True,
            runner=runner,
        )
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "0.4216" in msg


def test_logs_when_no_cost_summary(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api"), stdout=_gh_branch_listing([]))

    with caplog.at_level(logging.INFO, logger="tests.e2e_v1.cleanup"):
        teardown(
            root=tmp_path / "root",
            repo_slug="me/e2e-test",
            snapshot=RunSnapshot(pre_branches=set()),
            keep_root=True,
            runner=runner,
        )
    msg = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "no cost summary" in msg


# ---------------------------------------------------------------------------
# teardown — branch deletion via gh API
# ---------------------------------------------------------------------------


def test_deletes_only_new_branches(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(
        ("gh", "api"),
        stdout=_gh_branch_listing(
            ["main", "old-branch", "hammock/jobs/abc", "hammock/stages/abc/x"]
        ),
    )
    runner.expect(("gh", "pr", "list"), stdout="")
    teardown(
        root=tmp_path / "root",
        repo_slug="me/e2e-test",
        snapshot=RunSnapshot(pre_branches={"main", "old-branch"}),
        keep_root=True,
        runner=runner,
    )
    delete_calls = [
        c
        for c in runner.calls
        if c.args[:2] == ["gh", "api"]
        and "DELETE" in c.args
        and any("git/refs/heads/" in a for a in c.args)
    ]
    deleted = {
        a.split("git/refs/heads/", 1)[1]
        for c in delete_calls
        for a in c.args
        if "git/refs/heads/" in a
    }
    assert deleted == {"hammock/jobs/abc", "hammock/stages/abc/x"}


def test_continues_on_branch_delete_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runner = FakeRunner()
    runner.expect(
        ("gh", "api"),
        stdout=_gh_branch_listing(["main", "hammock/a", "hammock/b"]),
    )
    runner.expect(("gh", "pr", "list"), stdout="")
    runner.expect(
        ("gh", "api", "-X", "DELETE", "repos/me/e2e-test/git/refs/heads/hammock/a"),
        returncode=1,
        stderr="rejected",
    )

    with caplog.at_level(logging.WARNING, logger="tests.e2e_v1.cleanup"):
        teardown(
            root=tmp_path / "root",
            repo_slug="me/e2e-test",
            snapshot=RunSnapshot(pre_branches={"main"}),
            keep_root=True,
            runner=runner,
        )
    delete_calls = [
        c
        for c in runner.calls
        if c.args[:2] == ["gh", "api"]
        and "DELETE" in c.args
        and any("git/refs/heads/" in a for a in c.args)
    ]
    assert len(delete_calls) == 2  # both attempted
    warnings = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert "hammock/a" in warnings


# ---------------------------------------------------------------------------
# teardown — PR closure
# ---------------------------------------------------------------------------


def test_closes_open_prs_before_branch_delete(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api"), stdout=_gh_branch_listing(["main"]))
    runner.expect(("gh", "pr", "list"), stdout="42\n43")
    teardown(
        root=tmp_path / "root",
        repo_slug="me/e2e-test",
        snapshot=RunSnapshot(pre_branches={"main"}),
        keep_root=True,
        runner=runner,
    )
    close_calls = [c for c in runner.calls if c.args[:3] == ["gh", "pr", "close"]]
    assert len(close_calls) == 2
    assert "42" in close_calls[0].args
    assert "43" in close_calls[1].args


# ---------------------------------------------------------------------------
# teardown — root removal
# ---------------------------------------------------------------------------


def test_preserves_root_when_keep_flag_set(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "marker.txt").write_text("preserve me")
    runner = FakeRunner()
    runner.expect(("gh", "api"), stdout="")
    teardown(
        root=root,
        repo_slug="me/e2e-test",
        snapshot=RunSnapshot(pre_branches=set()),
        keep_root=True,
        runner=runner,
    )
    assert root.is_dir()


def test_removes_root_when_keep_flag_unset(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "marker.txt").write_text("delete me")
    runner = FakeRunner()
    runner.expect(("gh", "api"), stdout="")
    teardown(
        root=root,
        repo_slug="me/e2e-test",
        snapshot=RunSnapshot(pre_branches=set()),
        keep_root=False,
        runner=runner,
    )
    assert not root.exists()
