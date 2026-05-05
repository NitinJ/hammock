"""Cleanup helper for the e2e_v1 test repo.

Per-run teardown:
1. Log accrued cost (sum across cost_summary.json files under <root>/jobs/).
2. Close any PRs the agent opened (so the next run isn't inheriting them).
3. Delete branches added during the run (diff against a pre-run snapshot).
4. Remove the tmp root unless ``keep_root=True``.

All steps are best-effort: failures log + continue so they never mask the
underlying test failure.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


CmdRunner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(
    args: list[str], *, cwd: Path | None = None, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=check,
    )


@dataclass(frozen=True)
class RunSnapshot:
    pre_branches: set[str] = field(default_factory=set)


def _list_remote_branches(repo_slug: str, runner: CmdRunner) -> set[str]:
    result = runner(["gh", "api", f"repos/{repo_slug}/branches", "--jq", ".[].name", "--paginate"])
    if result.returncode != 0:
        log.warning(
            "could not list branches for %s: rc=%d stderr=%s",
            repo_slug,
            result.returncode,
            result.stderr,
        )
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def take_snapshot(repo_slug: str, *, runner: CmdRunner | None = None) -> RunSnapshot:
    runner = runner or _default_runner
    return RunSnapshot(pre_branches=_list_remote_branches(repo_slug, runner))


def _read_total_cost(root: Path) -> float | None:
    jobs_dir = root / "jobs"
    if not jobs_dir.is_dir():
        return None
    total: float | None = None
    for path in jobs_dir.glob("*/cost_summary.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("could not read %s: %s", path, exc)
            continue
        value = data.get("total_usd")
        if isinstance(value, int | float):
            total = (total or 0.0) + float(value)
    return total


def _close_open_prs(repo_slug: str, runner: CmdRunner) -> None:
    list_result = runner(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo_slug,
            "--state",
            "open",
            "--json",
            "number",
            "--jq",
            ".[].number",
            "--limit",
            "100",
        ]
    )
    if list_result.returncode != 0:
        log.warning(
            "could not list PRs for %s: rc=%d stderr=%s",
            repo_slug,
            list_result.returncode,
            list_result.stderr.strip(),
        )
        return
    for line in list_result.stdout.splitlines():
        num = line.strip()
        if not num:
            continue
        close_result = runner(["gh", "pr", "close", num, "--repo", repo_slug, "--delete-branch"])
        if close_result.returncode != 0:
            log.warning(
                "PR #%s close failed: rc=%d stderr=%s — continuing",
                num,
                close_result.returncode,
                close_result.stderr.strip(),
            )


def _delete_branch(repo_slug: str, branch: str, runner: CmdRunner) -> None:
    """Delete *branch* from the remote via gh API.

    We use ``gh api -X DELETE`` rather than ``git push --delete`` because
    the latter requires a clone whose ``origin`` points at the test repo;
    the test process's cwd is the dev repo, so a naked push --delete
    silently targets the wrong remote.
    """
    result = runner(["gh", "api", "-X", "DELETE", f"repos/{repo_slug}/git/refs/heads/{branch}"])
    if result.returncode != 0:
        log.warning(
            "branch delete failed for %s: rc=%d stderr=%s — continuing",
            branch,
            result.returncode,
            result.stderr.strip(),
        )


def teardown(
    *,
    root: Path,
    repo_slug: str,
    snapshot: RunSnapshot,
    keep_root: bool,
    runner: CmdRunner | None = None,
) -> None:
    """Unconditional teardown. Failures log + continue."""
    runner = runner or _default_runner

    # 1. Cost log first — visible even if downstream cleanup raises.
    total = _read_total_cost(root)
    if total is None:
        log.info("no cost summary found under %s/jobs/*", root)
    else:
        log.info("run cost: $%.4f total (cost_summary.json)", total)

    # 2. Close any PRs the agent opened. Must precede branch deletion:
    # GitHub refuses to delete a branch with an open PR pointing at it.
    _close_open_prs(repo_slug, runner)

    # 3. Branch diff + delete.
    current = _list_remote_branches(repo_slug, runner)
    new_branches = current - snapshot.pre_branches
    for branch in sorted(new_branches):
        _delete_branch(repo_slug, branch, runner)

    # 4. Root removal.
    if keep_root:
        log.info("keep_root=True — preserving %s for post-mortem", root)
        return
    if root.exists():
        try:
            shutil.rmtree(root)
        except OSError as exc:
            log.warning("could not remove %s: %s", root, exc)
