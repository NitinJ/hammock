"""Tests for ``tests.e2e.cleanup``.

Per docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step G:

- Pre-run snapshot of remote branches; teardown only deletes the diff.
- Cost summary read + log on teardown (success or failure).
- ``shutil.rmtree`` on root unless ``keep_root=True``.
- Cleanup failures are logged + don't mask the test failure.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import subprocess
from collections.abc import Callable
from pathlib import Path

from tests.e2e.cleanup import RunSnapshot, take_snapshot, teardown

# ---------------------------------------------------------------------------
# Fake CmdRunner (subset of step C / D infrastructure)
# ---------------------------------------------------------------------------


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

    def __call__(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, check
        self.calls.append(_Call(args=list(args)))
        best: tuple[str, ...] | None = None
        for prefix in self._handlers:
            if tuple(args[: len(prefix)]) == prefix:
                if best is None or len(prefix) > len(best):
                    best = prefix
        if best is None:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")
        return self._handlers[best](args)


def _gh_branch_listing(branches: list[str]) -> str:
    """Mimic ``gh api repos/<repo>/branches --jq '.[].name'`` output."""
    return "\n".join(branches)


# ---------------------------------------------------------------------------
# take_snapshot
# ---------------------------------------------------------------------------


def test_snapshot_records_pre_existing_branches() -> None:
    runner = FakeRunner()
    runner.expect(
        ("gh", "api"),
        stdout=_gh_branch_listing(["main", "feature-already-here"]),
    )
    snap = take_snapshot("me/e2e-test", runner=runner)
    assert isinstance(snap, RunSnapshot)
    assert snap.pre_branches == {"main", "feature-already-here"}


# ---------------------------------------------------------------------------
# teardown
# ---------------------------------------------------------------------------


def test_teardown_deletes_only_new_branches(tmp_path: Path) -> None:
    runner = FakeRunner()
    pre = ["main", "old-branch"]
    post = ["main", "old-branch", "hammock/jobs/abc", "hammock/stages/abc/x"]
    snap = RunSnapshot(pre_branches=set(pre))
    # First call inside teardown fetches current branches.
    runner.expect(("gh", "api"), stdout=_gh_branch_listing(post))
    # PR-close pre-step: empty list (no PRs to close in this scenario).
    runner.expect(("gh", "pr", "list"), stdout="")

    teardown(
        root=tmp_path / "root",
        repo_slug="me/e2e-test",
        snapshot=snap,
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
    deleted = {a.split("git/refs/heads/", 1)[1] for c in delete_calls for a in c.args if "git/refs/heads/" in a}
    assert deleted == {"hammock/jobs/abc", "hammock/stages/abc/x"}


def test_teardown_logs_cost_summary(tmp_path: Path, caplog: object) -> None:
    """When cost_summary.json exists at the standard path, the total
    is logged so operators see what the run cost."""
    import pytest as _pytest  # local to keep top-of-file imports lean

    job_dir = tmp_path / "root" / "jobs" / "j"
    job_dir.mkdir(parents=True)
    (job_dir / "cost_summary.json").write_text(json.dumps({"total_usd": 0.4216}))

    runner = FakeRunner()
    runner.expect(("gh", "api"), stdout=_gh_branch_listing([]))

    assert isinstance(caplog, _pytest.LogCaptureFixture)
    with caplog.at_level(logging.INFO, logger="tests.e2e.cleanup"):
        teardown(
            root=tmp_path / "root",
            repo_slug="me/e2e-test",
            snapshot=RunSnapshot(pre_branches=set()),
            keep_root=True,
            runner=runner,
        )

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "0.4216" in messages
    assert "cost" in messages.lower()


def test_teardown_logs_when_cost_summary_missing(tmp_path: Path, caplog: object) -> None:
    import pytest as _pytest

    runner = FakeRunner()
    runner.expect(("gh", "api"), stdout=_gh_branch_listing([]))

    assert isinstance(caplog, _pytest.LogCaptureFixture)
    with caplog.at_level(logging.INFO, logger="tests.e2e.cleanup"):
        teardown(
            root=tmp_path / "root",
            repo_slug="me/e2e-test",
            snapshot=RunSnapshot(pre_branches=set()),
            keep_root=True,
            runner=runner,
        )

    messages = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "no cost summary" in messages


def test_teardown_preserves_root_when_flag_set(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "marker.txt").write_text("preserve me")

    runner = FakeRunner()
    runner.expect(("gh", "api"), stdout=_gh_branch_listing([]))

    teardown(
        root=root,
        repo_slug="me/e2e-test",
        snapshot=RunSnapshot(pre_branches=set()),
        keep_root=True,
        runner=runner,
    )

    assert root.is_dir()
    assert (root / "marker.txt").is_file()


def test_teardown_removes_root_when_flag_unset(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "marker.txt").write_text("delete me")

    runner = FakeRunner()
    runner.expect(("gh", "api"), stdout=_gh_branch_listing([]))

    teardown(
        root=root,
        repo_slug="me/e2e-test",
        snapshot=RunSnapshot(pre_branches=set()),
        keep_root=False,
        runner=runner,
    )

    assert not root.exists()


def test_teardown_continues_on_branch_delete_failure(tmp_path: Path, caplog: object) -> None:
    """A failing ``gh api -X DELETE`` for one branch logs but doesn't
    block the others. (Race with concurrent ops on the test repo.)"""
    import pytest as _pytest

    runner = FakeRunner()
    runner.expect(
        ("gh", "api"),
        stdout=_gh_branch_listing(["main", "hammock/a", "hammock/b"]),
    )
    runner.expect(("gh", "pr", "list"), stdout="")
    # Make the first delete fail; the second should still run.
    runner.expect(
        (
            "gh",
            "api",
            "-X",
            "DELETE",
            "repos/me/e2e-test/git/refs/heads/hammock/a",
        ),
        returncode=1,
        stderr="remote rejected",
    )

    assert isinstance(caplog, _pytest.LogCaptureFixture)
    with caplog.at_level(logging.WARNING, logger="tests.e2e.cleanup"):
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
