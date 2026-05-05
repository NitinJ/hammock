"""Unit tests for tests/e2e_v1/preflight.py."""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable

import pytest

from tests.e2e_v1.preflight import (
    PreflightConfig,
    PreflightFailure,
    PreflightSkip,
    _slug_from_url,
    run_preflight,
)


@dataclasses.dataclass
class _Call:
    args: list[str]


class FakeRunner:
    """Minimal stub of the CmdRunner contract: records calls + serves
    pre-registered responses by command-prefix."""

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
        **_: object,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(_Call(list(args)))
        best: tuple[str, ...] | None = None
        for prefix in self._handlers:
            if tuple(args[: len(prefix)]) == prefix:
                if best is None or len(prefix) > len(best):
                    best = prefix
        if best is None:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")
        return self._handlers[best](args)


# ---------------------------------------------------------------------------
# Skip vs failure on opt-in
# ---------------------------------------------------------------------------


def test_skips_when_opt_in_unset() -> None:
    runner = FakeRunner()
    with pytest.raises(PreflightSkip):
        run_preflight(env={}, runner=runner)


def test_fails_when_git_missing() -> None:
    runner = FakeRunner()
    runner.expect(("git", "--version"), returncode=1)
    with pytest.raises(PreflightFailure, match="git not on PATH"):
        run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "1"}, runner=runner)


def test_fails_when_gh_unauthenticated() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "auth", "status"), returncode=1)
    with pytest.raises(PreflightFailure, match="gh CLI not authenticated"):
        run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "1"}, runner=runner)


# ---------------------------------------------------------------------------
# Repo URL resolution
# ---------------------------------------------------------------------------


def test_repo_url_explicit_env_var_used() -> None:
    runner = FakeRunner()
    # When HAMMOCK_E2E_TEST_REPO_URL is given, gh api user must NOT be called.
    runner.expect(("claude", "--help"), stdout="--output-format json")
    cfg = run_preflight(
        env={
            "HAMMOCK_E2E_REAL_CLAUDE": "1",
            "HAMMOCK_E2E_TEST_REPO_URL": "https://github.com/me/explicit-repo",
        },
        runner=runner,
    )
    assert cfg.repo_url == "https://github.com/me/explicit-repo"
    user_calls = [c for c in runner.calls if c.args[:3] == ["gh", "api", "user"]]
    assert user_calls == []


def test_repo_url_default_derived_from_gh_user() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice\n")
    runner.expect(("claude", "--help"), stdout="--output-format json")
    cfg = run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "1"}, runner=runner)
    assert cfg.repo_url == "https://github.com/alice/hammock-e2e-test"


def test_fails_when_gh_api_user_fails() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), returncode=1, stderr="api error")
    with pytest.raises(PreflightFailure, match="gh api user failed"):
        run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "1"}, runner=runner)


# ---------------------------------------------------------------------------
# Repo viewability — "not found" is allowed (bootstrap will create);
# anything else (auth/network) is failure.
# ---------------------------------------------------------------------------


def test_repo_not_found_is_ok_for_preflight() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice")
    runner.expect(
        ("gh", "repo", "view", "alice/hammock-e2e-test"),
        returncode=1,
        stderr="Could not resolve to a Repository",
    )
    runner.expect(("claude", "--help"), stdout="--output-format json")
    cfg = run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "1"}, runner=runner)
    assert isinstance(cfg, PreflightConfig)


def test_repo_view_other_failure_is_fatal() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice")
    runner.expect(
        ("gh", "repo", "view"),
        returncode=4,
        stderr="HTTP 401: Bad credentials",
    )
    with pytest.raises(PreflightFailure, match="not viewable"):
        run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "1"}, runner=runner)


# ---------------------------------------------------------------------------
# Claude binary
# ---------------------------------------------------------------------------


def test_claude_binary_default_is_claude() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice")
    runner.expect(("claude", "--help"), stdout="--output-format something")
    cfg = run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "1"}, runner=runner)
    assert cfg.claude_binary == "claude"


def test_claude_binary_overridable_via_env() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice")
    runner.expect(("/opt/claude",), stdout="--output-format json")
    cfg = run_preflight(
        env={"HAMMOCK_E2E_REAL_CLAUDE": "1", "HAMMOCK_CLAUDE_BINARY": "/opt/claude"},
        runner=runner,
    )
    assert cfg.claude_binary == "/opt/claude"


def test_fails_when_claude_help_missing_output_format_flag() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice")
    runner.expect(("claude", "--help"), stdout="(no useful flags here)")
    with pytest.raises(PreflightFailure, match="missing --output-format"):
        run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "1"}, runner=runner)


# ---------------------------------------------------------------------------
# Timeout
# ---------------------------------------------------------------------------


def test_timeout_default_is_30() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice")
    runner.expect(("claude",), stdout="--output-format json")
    cfg = run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "1"}, runner=runner)
    assert cfg.timeout_min == 30


def test_timeout_override_via_env() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice")
    runner.expect(("claude",), stdout="--output-format json")
    cfg = run_preflight(
        env={"HAMMOCK_E2E_REAL_CLAUDE": "1", "HAMMOCK_E2E_TIMEOUT_MIN": "120"},
        runner=runner,
    )
    assert cfg.timeout_min == 120


def test_invalid_timeout_is_fatal() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice")
    runner.expect(("claude",), stdout="--output-format json")
    with pytest.raises(PreflightFailure, match="positive int"):
        run_preflight(
            env={"HAMMOCK_E2E_REAL_CLAUDE": "1", "HAMMOCK_E2E_TIMEOUT_MIN": "0"},
            runner=runner,
        )


# ---------------------------------------------------------------------------
# keep_root flag
# ---------------------------------------------------------------------------


def test_keep_root_truthy_values() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice")
    runner.expect(("claude",), stdout="--output-format json")
    for val in ("1", "true", "True", "yes"):
        cfg = run_preflight(
            env={"HAMMOCK_E2E_REAL_CLAUDE": "1", "HAMMOCK_E2E_KEEP_ROOT": val},
            runner=runner,
        )
        assert cfg.keep_root is True, f"value {val!r} should be truthy"


def test_keep_root_default_false() -> None:
    runner = FakeRunner()
    runner.expect(("gh", "api", "user"), stdout="alice")
    runner.expect(("claude",), stdout="--output-format json")
    cfg = run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "1"}, runner=runner)
    assert cfg.keep_root is False


# ---------------------------------------------------------------------------
# slug_from_url helper
# ---------------------------------------------------------------------------


def test_slug_from_https_url() -> None:
    assert _slug_from_url("https://github.com/me/repo") == "me/repo"


def test_slug_from_https_url_with_dot_git() -> None:
    assert _slug_from_url("https://github.com/me/repo.git") == "me/repo"


def test_slug_from_already_slug_form() -> None:
    assert _slug_from_url("me/repo") == "me/repo"
