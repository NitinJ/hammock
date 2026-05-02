"""Shared fixtures + helpers for CLI tests."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli import _external

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def normalize(output: str) -> str:
    """Strip ANSI escapes and collapse whitespace.

    Rich auto-wraps at terminal width and re-emits color codes per line; a
    long red error message can be split across a wrap boundary, breaking
    naive ``substring in res.output`` assertions. Use ``normalize()`` to
    flatten the output for assertions.
    """
    return " ".join(_ANSI_RE.sub("", output).split())


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def hammock_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Set ``HAMMOCK_ROOT`` to a clean ``tmp_path`` and yield the root."""
    root = tmp_path / "hammock-root"
    root.mkdir()
    monkeypatch.setenv("HAMMOCK_ROOT", str(root))
    yield root


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    """A fake git repo at ``<tmp_path>/repo`` with a ``.git/`` marker."""
    repo = tmp_path / "MyRepo-2026"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


@pytest.fixture
def patch_external(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Patch every ``cli._external.*`` to ok-by-default returns.

    Tests can override per-call by reassigning the dict keys before invoking
    the CLI. The dict is kept in scope so tests can read what was set.
    """
    state: dict[str, object] = {
        "git_remote_url": "https://github.com/example/repo.git",
        "git_default_branch": "main",
        "git_working_tree_dirty": False,
        "git_is_repo": True,
        "gh_auth_ok": True,
        "gh_repo_view": True,
    }
    monkeypatch.setattr(_external, "git_remote_url", lambda repo: state["git_remote_url"])
    monkeypatch.setattr(_external, "git_default_branch", lambda repo: state["git_default_branch"])
    monkeypatch.setattr(
        _external, "git_working_tree_dirty", lambda repo: state["git_working_tree_dirty"]
    )
    monkeypatch.setattr(_external, "git_is_repo", lambda path: state["git_is_repo"])
    monkeypatch.setattr(_external, "gh_auth_ok", lambda: state["gh_auth_ok"])
    monkeypatch.setattr(_external, "gh_repo_view", lambda url: state["gh_repo_view"])
    return state
