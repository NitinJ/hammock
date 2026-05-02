"""Tests for ``cli.doctor`` and the ``hammock project doctor`` command."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from cli import doctor as _doctor
from cli.__main__ import app
from shared.models import ProjectConfig


def _project(repo: Path, *, slug: str = "myrepo-2026") -> ProjectConfig:
    return ProjectConfig(
        slug=slug,
        name=slug,
        repo_path=str(repo),
        remote_url="https://github.com/example/repo.git",
        default_branch="main",
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Library-level (run_full / run_light)
# ---------------------------------------------------------------------------


def test_run_full_returns_twelve_checks(
    fake_repo: Path,
    hammock_env: Path,
    patch_external: dict[str, object],
) -> None:
    report = _doctor.run_full(_project(fake_repo), root=hammock_env)
    assert len(report.checks) == 12
    assert {c.number for c in report.checks} == set(range(1, 13))


def test_run_light_returns_four_checks(
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    report = _doctor.run_light(_project(fake_repo))
    assert len(report.checks) == 4
    assert {c.number for c in report.checks} == {1, 2, 5, 8}


def test_status_pass_when_all_ok(
    fake_repo: Path,
    hammock_env: Path,
    patch_external: dict[str, object],
) -> None:
    # Make CLAUDE.md exist so check #7 passes.
    (fake_repo / "CLAUDE.md").write_text("# claude\n")
    # Override skeleton present + .gitignore set up so warns are clean.
    overrides = fake_repo / ".hammock"
    for sub in (
        "agent-overrides",
        "skill-overrides",
        "hook-overrides/quality",
        "job-template-overrides",
        "observatory",
    ):
        (overrides / sub).mkdir(parents=True, exist_ok=True)
    (fake_repo / ".gitignore").write_text(".hammock/\n")
    report = _doctor.run_full(_project(fake_repo), auto_fix=False, root=hammock_env)
    assert report.status == "pass", [
        (c.number, c.severity, c.passed, c.message) for c in report.checks
    ]


def test_status_fail_when_repo_path_missing(
    tmp_path: Path,
    hammock_env: Path,
    patch_external: dict[str, object],
) -> None:
    missing = tmp_path / "not-here"
    report = _doctor.run_full(_project(missing), root=hammock_env)
    assert report.status == "fail"
    fails = [c for c in report.checks if not c.passed and c.severity == "fail"]
    assert any(c.number == 1 for c in fails)


def test_auto_fix_creates_override_skeleton(
    fake_repo: Path,
    hammock_env: Path,
    patch_external: dict[str, object],
) -> None:
    # No .hammock/ at all initially.
    assert not (fake_repo / ".hammock").exists()
    report = _doctor.run_full(_project(fake_repo), auto_fix=True, root=hammock_env)
    assert (fake_repo / ".hammock" / "agent-overrides").is_dir()
    skeleton_check = next(c for c in report.checks if c.number == 8)
    assert skeleton_check.auto_fixed
    assert skeleton_check.severity == "info"


def test_auto_fix_appends_to_gitignore(
    fake_repo: Path,
    hammock_env: Path,
    patch_external: dict[str, object],
) -> None:
    (fake_repo / ".gitignore").write_text("dist/\n")
    _doctor.run_full(_project(fake_repo), auto_fix=True, root=hammock_env)
    contents = (fake_repo / ".gitignore").read_text()
    assert ".hammock/" in contents
    assert "dist/" in contents


def test_no_autofix_when_disabled(
    fake_repo: Path,
    hammock_env: Path,
    patch_external: dict[str, object],
) -> None:
    report = _doctor.run_full(_project(fake_repo), auto_fix=False, root=hammock_env)
    skeleton_check = next(c for c in report.checks if c.number == 8)
    assert not skeleton_check.passed
    assert not skeleton_check.auto_fixed
    assert not (fake_repo / ".hammock" / "agent-overrides").exists()


def test_run_full_completes_under_2s(
    fake_repo: Path,
    hammock_env: Path,
    patch_external: dict[str, object],
) -> None:
    """Acceptance criterion: full doctor < 2s on a clean project."""
    t0 = time.monotonic()
    _doctor.run_full(_project(fake_repo), root=hammock_env)
    elapsed = time.monotonic() - t0
    assert elapsed < 2.0, f"full doctor took {elapsed:.2f}s"


def test_run_light_completes_under_200ms(
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    """Acceptance criterion: light doctor < 200ms."""
    t0 = time.monotonic()
    _doctor.run_light(_project(fake_repo))
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2, f"light doctor took {elapsed * 1000:.1f}ms"


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------


def test_doctor_cli_runs_and_writes_back(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    # Register first.
    res_reg = cli_runner.invoke(app, ["project", "register", str(fake_repo)])
    assert res_reg.exit_code == 0, res_reg.output

    res = cli_runner.invoke(app, ["project", "doctor", "myrepo-2026", "--yes"])
    assert res.exit_code == 0, res.output
    assert "doctor" in res.output.lower()


def test_doctor_cli_json_output(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    cli_runner.invoke(app, ["project", "register", str(fake_repo)])
    res = cli_runner.invoke(app, ["project", "doctor", "myrepo-2026", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["slug"] == "myrepo-2026"
    assert data["tier"] == "full"
    assert data["status"] in {"pass", "warn", "fail"}
    assert len(data["checks"]) == 12


def test_doctor_cli_unknown_slug(
    cli_runner: CliRunner,
    hammock_env: Path,
) -> None:
    res = cli_runner.invoke(app, ["project", "doctor", "no-such-slug"])
    assert res.exit_code != 0
