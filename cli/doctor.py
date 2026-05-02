"""``hammock project doctor`` — health checks per design doc § Project Registry.

Two tiers:

- **Full** — twelve checks (fail / warn / info severities), runs on demand
  + UI load + post-register. Auto-fixes warn-level drift idempotently.
- **Light** — fast pre-job subset (checks 1, 2, 5, 8). Microseconds each
  except ``gh auth status`` (~100ms).

Stage 2 ships both tiers minus checks that depend on later infrastructure
(item 10 — orphaned worktrees in `hammock_root/worktrees/<slug>/` —
becomes meaningful once Stage 4 spawns Job Drivers; reported as ``info``
until then). Item 12 — Job Driver liveness — is also stubbed (``info``,
"no active jobs in this project" until Stage 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cli import _external
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import ProjectConfig

Severity = Literal["fail", "warn", "info"]
Tier = Literal["full", "light"]


@dataclass(frozen=True)
class CheckResult:
    """One check's outcome."""

    number: int
    severity: Severity
    name: str
    passed: bool
    message: str
    auto_fixed: bool = False


@dataclass(frozen=True)
class DoctorReport:
    """Aggregate of all checks for one project at one moment."""

    slug: str
    tier: Tier
    checks: list[CheckResult]
    ran_at: datetime

    @property
    def passed(self) -> bool:
        """True iff no ``fail``-severity check failed."""
        return not any(c.severity == "fail" and not c.passed for c in self.checks)

    @property
    def status(self) -> Literal["pass", "warn", "fail"]:
        """Aggregate status — informs ``project.json``'s ``last_health_check_status``."""
        if any(c.severity == "fail" and not c.passed for c in self.checks):
            return "fail"
        if any(c.severity == "warn" and not c.passed for c in self.checks):
            return "warn"
        return "pass"


# ---------------------------------------------------------------------------
# Light tier (pre-submit_job)
# ---------------------------------------------------------------------------


def run_light(project: ProjectConfig) -> DoctorReport:
    """Subset run before every job submit. Checks 1, 2, 5, 8."""
    checks: list[CheckResult] = []
    repo = Path(project.repo_path)

    checks.append(_check_repo_path_exists(1, repo))
    checks.append(_check_is_git_repo(2, repo))
    checks.append(_check_gh_auth(5))
    checks.append(_check_override_skeleton(8, repo, auto_fix=True))

    return DoctorReport(
        slug=project.slug,
        tier="light",
        checks=checks,
        ran_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Full tier
# ---------------------------------------------------------------------------


def run_full(
    project: ProjectConfig,
    *,
    auto_fix: bool = True,
    root: Path | None = None,
) -> DoctorReport:
    """Run all twelve checks. Auto-fixes warn-level drift when *auto_fix* is True."""
    checks: list[CheckResult] = []
    repo = Path(project.repo_path)

    checks.append(_check_repo_path_exists(1, repo))
    checks.append(_check_is_git_repo(2, repo))
    checks.append(_check_remote_url_matches(3, project, repo))
    checks.append(_check_remote_reachable(4, project))
    checks.append(_check_gh_auth(5))
    checks.append(_check_default_branch(6, project, repo))
    checks.append(_check_claude_md(7, repo))
    checks.append(_check_override_skeleton(8, repo, auto_fix=auto_fix))
    checks.append(_check_gitignore_excludes_hammock(9, repo, auto_fix=auto_fix))
    checks.append(_check_no_orphaned_worktrees(10, project, root=root))
    checks.append(_check_no_stale_skill_symlinks(11, project))
    checks.append(_check_job_driver_liveness(12, project, root=root))

    return DoctorReport(
        slug=project.slug,
        tier="full",
        checks=checks,
        ran_at=datetime.now(timezone.utc),
    )


def write_back(report: DoctorReport, project: ProjectConfig, *, root: Path | None = None) -> None:
    """Persist ``last_health_check_at`` and ``last_health_check_status`` in project.json."""
    updated = project.model_copy(
        update={
            "last_health_check_at": report.ran_at,
            "last_health_check_status": report.status,
        }
    )
    atomic_write_json(paths.project_json(project.slug, root=root), updated)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_repo_path_exists(n: int, repo: Path) -> CheckResult:
    ok = repo.exists() and repo.is_dir()
    return CheckResult(
        number=n,
        severity="fail",
        name="repo path exists",
        passed=ok,
        message=f"{repo}" if ok else f"{repo} does not exist",
    )


def _check_is_git_repo(n: int, repo: Path) -> CheckResult:
    ok = _external.git_is_repo(repo)
    return CheckResult(
        number=n,
        severity="fail",
        name="is git repository",
        passed=ok,
        message="ok" if ok else f"{repo}/.git is missing",
    )


def _check_remote_url_matches(n: int, project: ProjectConfig, repo: Path) -> CheckResult:
    actual = _external.git_remote_url(repo)
    expected = project.remote_url
    if expected is None:
        return CheckResult(n, "warn", "remote_url matches", True, "(no remote configured)")
    if actual is None:
        return CheckResult(n, "warn", "remote_url matches", False, "git remote get-url origin failed")
    return CheckResult(
        number=n,
        severity="warn",
        name="remote_url matches",
        passed=actual == expected,
        message="ok" if actual == expected else f"stored={expected!r} actual={actual!r}",
    )


def _check_remote_reachable(n: int, project: ProjectConfig) -> CheckResult:
    if not project.remote_url:
        return CheckResult(n, "fail", "remote reachable", True, "(no remote configured)")
    ok = _external.gh_repo_view(project.remote_url)
    return CheckResult(
        number=n,
        severity="fail",
        name="remote reachable",
        passed=ok,
        message="ok" if ok else f"gh repo view {project.remote_url} failed",
    )


def _check_gh_auth(n: int) -> CheckResult:
    ok = _external.gh_auth_ok()
    return CheckResult(
        number=n,
        severity="fail",
        name="gh auth status",
        passed=ok,
        message="ok" if ok else "gh auth login required",
    )


def _check_default_branch(n: int, project: ProjectConfig, repo: Path) -> CheckResult:
    actual = _external.git_default_branch(repo)
    if actual is None:
        return CheckResult(n, "warn", "default branch detectable", False, "could not detect")
    return CheckResult(
        number=n,
        severity="warn",
        name="default branch matches",
        passed=actual == project.default_branch,
        message="ok"
        if actual == project.default_branch
        else f"stored={project.default_branch!r} actual={actual!r}",
    )


def _check_claude_md(n: int, repo: Path) -> CheckResult:
    ok = (repo / "CLAUDE.md").exists()
    return CheckResult(
        number=n,
        severity="warn",
        name="CLAUDE.md present",
        passed=ok,
        message="ok" if ok else "CLAUDE.md not found at repo root",
    )


_OVERRIDE_SUBDIRS = (
    "agent-overrides",
    "skill-overrides",
    "hook-overrides/quality",
    "job-template-overrides",
    "observatory",
)


def _check_override_skeleton(n: int, repo: Path, *, auto_fix: bool) -> CheckResult:
    base = paths.project_overrides_root(repo)
    missing = [d for d in _OVERRIDE_SUBDIRS if not (base / d).is_dir()]
    if not missing:
        return CheckResult(n, "warn", "override skeleton intact", True, "ok")
    if auto_fix:
        for d in missing:
            (base / d).mkdir(parents=True, exist_ok=True)
        return CheckResult(
            n,
            "info",
            "override skeleton intact",
            True,
            f"created missing dirs: {', '.join(missing)}",
            auto_fixed=True,
        )
    return CheckResult(
        n, "warn", "override skeleton intact", False, f"missing: {', '.join(missing)}"
    )


def _check_gitignore_excludes_hammock(n: int, repo: Path, *, auto_fix: bool) -> CheckResult:
    gi = repo / ".gitignore"
    contains = False
    if gi.exists():
        contains = any(
            line.strip() in {".hammock/", ".hammock"} for line in gi.read_text().splitlines()
        )
    if contains:
        return CheckResult(n, "warn", ".gitignore excludes .hammock/", True, "ok")
    if auto_fix:
        existing = gi.read_text() if gi.exists() else ""
        sep = "" if existing.endswith("\n") or existing == "" else "\n"
        gi.write_text(existing + sep + ".hammock/\n")
        return CheckResult(
            n,
            "info",
            ".gitignore excludes .hammock/",
            True,
            "appended '.hammock/' to .gitignore",
            auto_fixed=True,
        )
    return CheckResult(
        n, "warn", ".gitignore excludes .hammock/", False, "missing entry .hammock/"
    )


def _check_no_orphaned_worktrees(
    n: int, project: ProjectConfig, *, root: Path | None
) -> CheckResult:
    # Stage 4+ creates worktrees under hammock_root/worktrees/<slug>/. v0 stage 2:
    # report ok / report orphans if the dir somehow exists.
    base = paths.hammock_root(root) / "worktrees" / project.slug
    if not base.exists():
        return CheckResult(n, "warn", "no orphaned worktrees", True, "ok")
    entries = list(base.iterdir())
    if not entries:
        return CheckResult(n, "warn", "no orphaned worktrees", True, "ok")
    return CheckResult(
        n,
        "warn",
        "no orphaned worktrees",
        False,
        f"{len(entries)} entries under {base}",
    )


def _check_no_stale_skill_symlinks(n: int, project: ProjectConfig) -> CheckResult:
    skills_root = Path.home() / ".claude" / "skills"
    if not skills_root.is_dir():
        return CheckResult(n, "warn", "no stale skill symlinks", True, "(~/.claude/skills/ absent)")
    prefix = f"{project.slug}__"
    candidates = [p for p in skills_root.iterdir() if p.name.startswith(prefix)]
    stale = [p for p in candidates if p.is_symlink() and not p.resolve(strict=False).exists()]
    if not stale:
        return CheckResult(n, "warn", "no stale skill symlinks", True, "ok")
    return CheckResult(
        n,
        "warn",
        "no stale skill symlinks",
        False,
        f"{len(stale)} dangling: {', '.join(p.name for p in stale)}",
    )


def _check_job_driver_liveness(
    n: int, project: ProjectConfig, *, root: Path | None
) -> CheckResult:
    # Stage 4 is when this becomes meaningful. Stage 2: count active jobs in
    # the project; if any have a stale heartbeat, surface it. v0 stub: count.
    jobs_dir = paths.jobs_dir(root)
    if not jobs_dir.is_dir():
        return CheckResult(n, "info", "Job Driver liveness", True, "no jobs directory")
    matching = 0
    for j in jobs_dir.iterdir():
        if not j.is_dir():
            continue
        cfg = j / "job.json"
        if cfg.exists():
            try:
                # Lightweight check: project_slug substring in JSON. Avoids importing
                # JobConfig + parsing every job here. Stage 4+ replaces this with
                # a live heartbeat test.
                if f'"project_slug":"{project.slug}"' in cfg.read_text().replace(" ", ""):
                    matching += 1
            except OSError:
                pass
    return CheckResult(
        n,
        "info",
        "Job Driver liveness",
        True,
        f"{matching} active jobs (Stage 4 will validate heartbeats)",
    )
