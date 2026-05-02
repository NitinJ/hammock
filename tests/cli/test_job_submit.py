"""Tests for ``hammock job submit``."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cli.__main__ import app
from shared import paths
from tests.cli.conftest import normalize


def _register(runner: CliRunner, fake_repo: Path) -> None:
    res = runner.invoke(app, ["project", "register", str(fake_repo)])
    assert res.exit_code == 0, res.output


def test_job_submit_dry_run_emits_plan(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = cli_runner.invoke(
        app,
        [
            "job",
            "submit",
            "--project",
            "myrepo-2026",
            "--type",
            "build-feature",
            "--title",
            "add invite onboarding",
            "--request-text",
            "Build the invite-only onboarding flow.",
            "--dry-run",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["ok"] is True
    assert data["dry_run"] is True
    assert data["job_slug"].endswith("-add-invite-onboarding")
    # No job dir written
    assert not paths.job_dir(data["job_slug"], root=hammock_env).exists()


def test_job_submit_writes_job_dir(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = cli_runner.invoke(
        app,
        [
            "job",
            "submit",
            "--project",
            "myrepo-2026",
            "--type",
            "build-feature",
            "--title",
            "add caching",
            "--request-text",
            "Add caching to the API endpoints.",
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    job_slug = data["job_slug"]
    assert paths.job_json(job_slug, root=hammock_env).exists()
    assert paths.job_prompt(job_slug, root=hammock_env).exists()
    assert paths.job_stage_list(job_slug, root=hammock_env).exists()


def test_job_submit_unknown_project_fails(
    cli_runner: CliRunner,
    hammock_env: Path,
) -> None:
    res = cli_runner.invoke(
        app,
        [
            "job",
            "submit",
            "--project",
            "nope",
            "--type",
            "build-feature",
            "--title",
            "x",
            "--request-text",
            "y",
            "--json",
        ],
    )
    assert res.exit_code != 0
    data = json.loads(res.output)
    assert data["ok"] is False
    assert any(f["kind"] == "project_not_found" for f in data["failures"])


def test_job_submit_unknown_job_type_fails(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    res = cli_runner.invoke(
        app,
        [
            "job",
            "submit",
            "--project",
            "myrepo-2026",
            "--type",
            "fake-type",
            "--title",
            "x",
            "--request-text",
            "y",
            "--json",
        ],
    )
    assert res.exit_code != 0
    data = json.loads(res.output)
    assert any(f["kind"] == "template_not_found" for f in data["failures"])


def test_job_submit_request_file_read(
    cli_runner: CliRunner,
    hammock_env: Path,
    fake_repo: Path,
    tmp_path: Path,
    patch_external: dict[str, object],
) -> None:
    _register(cli_runner, fake_repo)
    request_file = tmp_path / "request.md"
    request_file.write_text("From a file.")
    res = cli_runner.invoke(
        app,
        [
            "job",
            "submit",
            "--project",
            "myrepo-2026",
            "--type",
            "build-feature",
            "--title",
            "from file",
            "--request-file",
            str(request_file),
            "--json",
        ],
    )
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert paths.job_prompt(data["job_slug"], root=hammock_env).read_text() == "From a file."


def test_job_submit_requires_request_input(
    cli_runner: CliRunner,
    hammock_env: Path,
) -> None:
    res = cli_runner.invoke(
        app,
        [
            "job",
            "submit",
            "--project",
            "x",
            "--type",
            "build-feature",
            "--title",
            "x",
        ],
    )
    assert res.exit_code != 0
    assert "request-text or --request-file" in normalize(res.output)
