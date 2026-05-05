"""Unit tests for tests/e2e_v1/bootstrap.py."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.e2e_v1.bootstrap import (
    BootstrapResult,
    RepoBootstrapError,
    _slug_from_url,
    bootstrap_test_repo,
)


@dataclasses.dataclass
class _Call:
    args: list[str]
    cwd: Path | None
    check: bool


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
        self.calls.append(_Call(list(args), cwd, check))
        # Side-effect: `git clone <url> <dest>` mimics real-clone by
        # creating <dest> as an empty dir, so subsequent file copies into
        # it succeed in unit tests.
        if args[:2] == ["git", "clone"] and len(args) >= 4:
            dest = Path(args[3])
            dest.mkdir(parents=True, exist_ok=True)
        best: tuple[str, ...] | None = None
        for prefix in self._handlers:
            if tuple(args[: len(prefix)]) == prefix:
                if best is None or len(prefix) > len(best):
                    best = prefix
        if best is None:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")
        return self._handlers[best](args)


def _seed(tmp_path: Path) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "README.md").write_text("seed readme\n")
    (seed / "main.py").write_text("# main\n")
    return seed


# ---------------------------------------------------------------------------
# Reuse path: gh repo view succeeds → no creation, no clone
# ---------------------------------------------------------------------------


def test_reuses_existing_repo_when_view_succeeds(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("gh", "repo", "view", "me/hammock-e2e-test"), stdout="exists")
    result = bootstrap_test_repo(
        "https://github.com/me/hammock-e2e-test",
        seed_dir=_seed(tmp_path),
        runner=runner,
    )
    assert result == BootstrapResult(
        created=False,
        repo_url="https://github.com/me/hammock-e2e-test",
        repo_slug="me/hammock-e2e-test",
    )
    create_calls = [c for c in runner.calls if c.args[:3] == ["gh", "repo", "create"]]
    clone_calls = [c for c in runner.calls if c.args[:2] == ["git", "clone"]]
    assert create_calls == [] and clone_calls == []


# ---------------------------------------------------------------------------
# Create path: gh repo view fails with not-found → create + seed + push
# ---------------------------------------------------------------------------


def test_creates_repo_when_not_found(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(
        ("gh", "repo", "view"),
        returncode=1,
        stderr="GraphQL: Could not resolve to a Repository (404)",
    )
    result = bootstrap_test_repo(
        "https://github.com/me/hammock-e2e-test",
        seed_dir=_seed(tmp_path),
        runner=runner,
    )
    assert result.created is True
    create_calls = [c for c in runner.calls if c.args[:3] == ["gh", "repo", "create"]]
    assert len(create_calls) == 1
    assert "me/hammock-e2e-test" in create_calls[0].args
    push_calls = [
        c for c in runner.calls if c.args[:3] == ["git", "push", "-u"]
    ]
    assert len(push_calls) == 1


def test_create_path_forces_main_branch(tmp_path: Path) -> None:
    """Some gh accounts have non-main defaults. After clone we force `main`."""
    runner = FakeRunner()
    runner.expect(("gh", "repo", "view"), returncode=1, stderr="HTTP 404")
    bootstrap_test_repo(
        "https://github.com/me/hammock-e2e-test",
        seed_dir=_seed(tmp_path),
        runner=runner,
    )
    checkout_calls = [
        c
        for c in runner.calls
        if c.args[:3] == ["git", "checkout", "-B"] and "main" in c.args
    ]
    assert len(checkout_calls) == 1


# ---------------------------------------------------------------------------
# Failure path: gh repo view fails with non-not-found stderr → raise
# ---------------------------------------------------------------------------


def test_other_view_failures_raise(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(
        ("gh", "repo", "view"),
        returncode=4,
        stderr="HTTP 401: Bad credentials",
    )
    with pytest.raises(RepoBootstrapError, match="auth/network/other"):
        bootstrap_test_repo(
            "https://github.com/me/hammock-e2e-test",
            seed_dir=_seed(tmp_path),
            runner=runner,
        )


def test_missing_seed_dir_is_fatal(tmp_path: Path) -> None:
    runner = FakeRunner()
    with pytest.raises(RepoBootstrapError, match="seed_dir does not exist"):
        bootstrap_test_repo(
            "https://github.com/me/hammock-e2e-test",
            seed_dir=tmp_path / "no-such-dir",
            runner=runner,
        )


# ---------------------------------------------------------------------------
# Slug parsing
# ---------------------------------------------------------------------------


def test_slug_from_https_url() -> None:
    assert _slug_from_url("https://github.com/me/repo") == "me/repo"


def test_slug_from_https_url_with_dot_git() -> None:
    assert _slug_from_url("https://github.com/me/repo.git") == "me/repo"


def test_slug_from_already_slug_form() -> None:
    assert _slug_from_url("me/repo") == "me/repo"


def test_slug_rejects_malformed() -> None:
    with pytest.raises(RepoBootstrapError, match="could not parse"):
        _slug_from_url("https://github.com/")
