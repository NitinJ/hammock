"""Cleanup helper for the real-claude e2e test.

Per docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step G and
spec D6+D14:

- :func:`take_snapshot` records the test repo's pre-existing remote
  branches so teardown only touches what this run created.
- :func:`teardown` runs unconditionally via the test fixture's
  finaliser. It logs the run's accrued cost (from
  ``cost_summary.json``), deletes any branches absent in the pre-run
  snapshot, and removes the tmp root unless ``keep_root=True``.
- All cleanup failures are logged + swallowed so they never mask the
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
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=check,
    )


@dataclass(frozen=True)
class RunSnapshot:
    pre_branches: set[str] = field(default_factory=set)


def _list_remote_branches(repo_slug: str, runner: CmdRunner) -> set[str]:
    """List remote branch names via ``gh api``.

    Returns an empty set on any failure; the caller logs.
    """
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
    """Record the repo's current remote branches for teardown diff."""
    if runner is None:
        runner = _default_runner
    return RunSnapshot(pre_branches=_list_remote_branches(repo_slug, runner))


def _read_total_cost(root: Path) -> float | None:
    """Walk ``<root>/jobs/*/cost_summary.json`` and sum totals.

    Returns None when no cost_summary.json files exist; otherwise the
    aggregate total for visibility on teardown (D14).
    """
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


def _delete_branch(repo_slug: str, branch: str, runner: CmdRunner) -> None:
    """Delete *branch* from the remote via the GitHub API.

    We use ``gh api -X DELETE /repos/<slug>/git/refs/heads/<branch>``
    rather than ``git push --delete`` because the latter requires the
    teardown process to be inside (or have its cwd configured for) a
    clone whose ``origin`` points at the test repo. The cleanup
    fixture runs from the pytest process's cwd, which is the *hammock
    dev repo* (origin = the hammock platform itself), so a naked
    ``git push --delete origin <branch>`` would silently target the
    wrong remote and report "remote ref does not exist".
    """
    result = runner(
        [
            "gh",
            "api",
            "-X",
            "DELETE",
            f"repos/{repo_slug}/git/refs/heads/{branch}",
        ]
    )
    if result.returncode != 0:
        log.warning(
            "branch delete failed for %s: rc=%d stderr=%s — continuing",
            branch,
            result.returncode,
            result.stderr.strip(),
        )


def _close_open_prs(repo_slug: str, runner: CmdRunner) -> None:
    """Close every open PR on *repo_slug* — call from teardown.

    Across reuse-the-same-repo runs, agents open real PRs against
    main. If we don't close them on teardown, the next run inherits
    a wall of stale open PRs and (worse) the branches behind them
    can't be deleted via ``DELETE /git/refs/heads/<branch>`` because
    GitHub blocks branch-deletion on branches with open PRs.
    Best-effort; logs and continues on any failure.
    """
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


def teardown(
    *,
    root: Path,
    repo_slug: str,
    snapshot: RunSnapshot,
    keep_root: bool,
    runner: CmdRunner | None = None,
) -> None:
    """Unconditional teardown. Failures log + continue."""
    if runner is None:
        runner = _default_runner

    # 1. Cost log first — visible even if downstream cleanup raises.
    total = _read_total_cost(root)
    if total is None:
        log.info("no cost summary found under %s/jobs/*", root)
    else:
        log.info("run cost: $%.4f total (cost_summary.json)", total)

    # 2. Close any PRs the agent opened (must precede branch deletion:
    # GitHub refuses to delete a branch with an open PR pointing at it).
    _close_open_prs(repo_slug, runner)

    # 3. Branch diff + delete.
    current = _list_remote_branches(repo_slug, runner)
    new_branches = current - snapshot.pre_branches
    for branch in sorted(new_branches):
        _delete_branch(repo_slug, branch, runner)

    # 3. Root removal.
    if keep_root:
        log.info("keep_root=True — preserving %s for post-mortem", root)
        return
    if root.exists():
        try:
            shutil.rmtree(root)
        except OSError as exc:
            log.warning("could not remove %s: %s", root, exc)
