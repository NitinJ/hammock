"""Tests for ``hammock project list/show/rename/relocate/deregister``."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cli.__main__ import app
from shared import paths
from tests.cli.conftest import normalize


def _invoke(runner: CliRunner, *args: str, input: str | None = None) -> object:
    return runner.invoke(app, list(args), input=input)


def _register(runner: CliRunner, fake_repo: Path, slug: str | None = None) -> None:
    args = ["project", "register", str(fake_repo)]
    if slug is not None:
        args += ["--slug", slug]
    res = runner.invoke(app, args)
    assert res.exit_code == 0, res.output


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def test_list_empty(cli_runner: CliRunner, hammock_env: Path) -> None:
    res = _invoke(cli_runner, "project", "list")
    assert res.exit_code == 0
    assert "no projects" in normalize(res.output).lower()


def test_list_renders_table(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = _invoke(cli_runner, "project", "list")
    assert res.exit_code == 0
    out = normalize(res.output)
    assert "myrepo-2026" in out
    assert "main" in out


def test_list_json(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = _invoke(cli_runner, "project", "list", "--json")
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["slug"] == "myrepo-2026"


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_pretty(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = _invoke(cli_runner, "project", "show", "myrepo-2026")
    assert res.exit_code == 0
    out = normalize(res.output)
    assert "myrepo-2026" in out
    assert "https://github.com/example/repo.git" in out


def test_show_json(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = _invoke(cli_runner, "project", "show", "myrepo-2026", "--json")
    assert res.exit_code == 0
    data = json.loads(res.output)
    assert data["slug"] == "myrepo-2026"


def test_show_unknown_slug(cli_runner: CliRunner, hammock_env: Path) -> None:
    res = _invoke(cli_runner, "project", "show", "no-such-slug")
    assert res.exit_code != 0
    assert "no project" in normalize(res.output).lower()


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------


def test_rename_updates_name(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = _invoke(cli_runner, "project", "rename", "myrepo-2026", "Friendlier Name")
    assert res.exit_code == 0
    data = json.loads(paths.project_json("myrepo-2026", root=hammock_env).read_text())
    assert data["name"] == "Friendlier Name"
    # slug unchanged
    assert data["slug"] == "myrepo-2026"


def test_rename_rejects_empty_name(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = _invoke(cli_runner, "project", "rename", "myrepo-2026", "   ")
    assert res.exit_code != 0


def test_rename_unknown_slug(cli_runner: CliRunner, hammock_env: Path) -> None:
    res = _invoke(cli_runner, "project", "rename", "no-such", "x")
    assert res.exit_code != 0


# ---------------------------------------------------------------------------
# relocate
# ---------------------------------------------------------------------------


def test_relocate_updates_path(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    tmp_path: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    new_path = tmp_path / "moved" / "MyRepo-2026"
    new_path.mkdir(parents=True)
    (new_path / ".git").mkdir()

    res = _invoke(cli_runner, "project", "relocate", "myrepo-2026", str(new_path))
    assert res.exit_code == 0, res.output

    data = json.loads(paths.project_json("myrepo-2026", root=hammock_env).read_text())
    assert data["repo_path"] == str(new_path)


def test_relocate_rejects_remote_mismatch_without_force(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    tmp_path: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    new_path = tmp_path / "moved" / "MyRepo-2026"
    new_path.mkdir(parents=True)
    (new_path / ".git").mkdir()

    # Now the new path's remote is different
    patch_external["git_remote_url"] = "https://github.com/another/repo.git"
    res = _invoke(cli_runner, "project", "relocate", "myrepo-2026", str(new_path))
    assert res.exit_code != 0
    assert "remote mismatch" in normalize(res.output).lower()


def test_relocate_force_skips_remote_check(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    tmp_path: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    new_path = tmp_path / "moved" / "MyRepo-2026"
    new_path.mkdir(parents=True)
    (new_path / ".git").mkdir()

    patch_external["git_remote_url"] = "https://github.com/another/repo.git"
    res = _invoke(cli_runner, "project", "relocate", "myrepo-2026", str(new_path), "--force")
    assert res.exit_code == 0, res.output


# ---------------------------------------------------------------------------
# deregister
# ---------------------------------------------------------------------------


def test_deregister_with_yes_removes_everything(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    assert (fake_repo / ".hammock").exists()
    assert paths.project_dir("myrepo-2026", root=hammock_env).exists()

    res = _invoke(cli_runner, "project", "deregister", "myrepo-2026", "--yes")
    assert res.exit_code == 0, res.output

    assert not paths.project_dir("myrepo-2026", root=hammock_env).exists()
    assert not (fake_repo / ".hammock").exists()


def test_deregister_keep_overrides(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = _invoke(
        cli_runner,
        "project",
        "deregister",
        "myrepo-2026",
        "--yes",
        "--keep-overrides",
    )
    assert res.exit_code == 0
    assert not paths.project_dir("myrepo-2026", root=hammock_env).exists()
    assert (fake_repo / ".hammock").exists()


def test_deregister_declined_at_prompt(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = _invoke(cli_runner, "project", "deregister", "myrepo-2026", input="n\n")
    assert res.exit_code != 0
    assert paths.project_dir("myrepo-2026", root=hammock_env).exists()


def test_deregister_unknown_slug(cli_runner: CliRunner, hammock_env: Path) -> None:
    res = _invoke(cli_runner, "project", "deregister", "no-such", "--yes")
    assert res.exit_code != 0
