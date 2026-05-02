"""Tests for ``hammock project register``."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cli.__main__ import app
from shared import paths
from tests.cli.conftest import normalize


def _invoke(runner: CliRunner, *args: str) -> object:
    return runner.invoke(app, list(args))


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------


def test_register_creates_project_json_and_overrides(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    res = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res.exit_code == 0, res.output

    pj = paths.project_json("myrepo-2026", root=hammock_env)
    assert pj.exists()
    data = json.loads(pj.read_text())
    assert data["slug"] == "myrepo-2026"
    assert data["repo_path"] == str(fake_repo)
    assert data["default_branch"] == "main"
    assert data["remote_url"] == "https://github.com/example/repo.git"

    overrides = fake_repo / ".hammock"
    assert (overrides / "agent-overrides").is_dir()
    assert (overrides / "skill-overrides").is_dir()
    assert (overrides / "hook-overrides" / "quality").is_dir()
    assert (overrides / "job-template-overrides").is_dir()
    assert (overrides / "observatory").is_dir()
    assert (overrides / "README.md").exists()


def test_register_appends_to_gitignore(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    (fake_repo / ".gitignore").write_text("dist/\n")
    res = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res.exit_code == 0, res.output

    contents = (fake_repo / ".gitignore").read_text()
    assert ".hammock/" in contents
    assert "dist/" in contents  # preserved


def test_register_creates_gitignore_if_missing(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    res = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res.exit_code == 0
    assert ".hammock/" in (fake_repo / ".gitignore").read_text()


def test_register_does_not_duplicate_gitignore_entry(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    (fake_repo / ".gitignore").write_text(".hammock/\n")
    res = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res.exit_code == 0
    assert (fake_repo / ".gitignore").read_text().count(".hammock") == 1


def test_register_creates_project_repo_symlink(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    res = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res.exit_code == 0
    sym = paths.project_dir("myrepo-2026", root=hammock_env) / "project_repo"
    assert sym.is_symlink()
    assert sym.resolve() == fake_repo


def test_register_with_explicit_slug(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    res = _invoke(cli_runner, "project", "register", str(fake_repo), "--slug", "custom-slug")
    assert res.exit_code == 0
    assert paths.project_json("custom-slug", root=hammock_env).exists()
    assert not paths.project_json("myrepo-2026", root=hammock_env).exists()


def test_register_with_explicit_name(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    res = _invoke(cli_runner, "project", "register", str(fake_repo), "--name", "Display Name")
    assert res.exit_code == 0
    data = json.loads(paths.project_json("myrepo-2026", root=hammock_env).read_text())
    assert data["name"] == "Display Name"


def test_register_runs_initial_doctor_writes_back(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    res = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res.exit_code == 0
    data = json.loads(paths.project_json("myrepo-2026", root=hammock_env).read_text())
    assert data["last_health_check_at"] is not None
    assert data["last_health_check_status"] in {"pass", "warn", "fail"}


# ---------------------------------------------------------------------------
# error paths
# ---------------------------------------------------------------------------


def test_register_rejects_missing_path(
    cli_runner: CliRunner,
    hammock_env: Path,
    tmp_path: Path,
    patch_external: dict[str, object],
) -> None:
    res = _invoke(cli_runner, "project", "register", str(tmp_path / "does-not-exist"))
    assert res.exit_code != 0
    assert "does not exist" in normalize(res.output)


def test_register_rejects_non_git(
    cli_runner: CliRunner,
    hammock_env: Path,
    tmp_path: Path,
    patch_external: dict[str, object],
) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()
    patch_external["git_is_repo"] = False
    res = _invoke(cli_runner, "project", "register", str(not_a_repo))
    assert res.exit_code != 0
    assert "not a git repository" in normalize(res.output)


def test_register_rejects_when_remote_unconfigured(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    patch_external["git_remote_url"] = None
    res = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res.exit_code != 0
    assert "remote" in normalize(res.output).lower()


def test_register_rejects_when_gh_auth_failed(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    patch_external["gh_auth_ok"] = False
    res = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res.exit_code != 0
    assert "gh auth" in normalize(res.output).lower()


def test_register_rejects_when_remote_unreachable(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    patch_external["gh_repo_view"] = False
    res = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res.exit_code != 0
    assert "not reachable" in normalize(res.output).lower()


def test_register_skip_remote_checks_bypasses_gh(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    patch_external["gh_auth_ok"] = False
    patch_external["gh_repo_view"] = False
    res = _invoke(cli_runner, "project", "register", str(fake_repo), "--skip-remote-checks")
    assert res.exit_code == 0, res.output


def test_register_rejects_invalid_explicit_slug(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    res = _invoke(cli_runner, "project", "register", str(fake_repo), "--slug", "Bad Slug")
    assert res.exit_code != 0
    assert "invalid slug" in normalize(res.output).lower()


def test_register_rejects_slug_collision(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    tmp_path: Path,
    patch_external: dict[str, object],
) -> None:
    # First registration: fake_repo at tmp_path/MyRepo-2026 → slug "myrepo-2026".
    res1 = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res1.exit_code == 0

    # Second: distinct path, same basename → same derived slug → collision.
    other = tmp_path / "siblings" / "MyRepo-2026"
    other.mkdir(parents=True)
    (other / ".git").mkdir()
    assert other != fake_repo  # sanity

    res2 = _invoke(cli_runner, "project", "register", str(other))
    assert res2.exit_code != 0, res2.output
    assert "already taken" in normalize(res2.output).lower()


def test_register_idempotent_on_same_path(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    res1 = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res1.exit_code == 0
    res2 = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res2.exit_code == 0
    assert "already registered" in normalize(res2.output).lower()


def test_register_rejects_undetectable_default_branch(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    patch_external["git_default_branch"] = None
    res = _invoke(cli_runner, "project", "register", str(fake_repo))
    assert res.exit_code != 0
    assert "default branch" in normalize(res.output).lower()


def test_register_with_explicit_default_branch(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    patch_external["git_default_branch"] = None
    res = _invoke(
        cli_runner,
        "project",
        "register",
        str(fake_repo),
        "--default-branch",
        "develop",
    )
    assert res.exit_code == 0, res.output
    data = json.loads(paths.project_json("myrepo-2026", root=hammock_env).read_text())
    assert data["default_branch"] == "develop"
