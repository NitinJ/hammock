"""Tests for ``tests.e2e.preflight``.

Per docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step D:
- Opt-in env var unset → ``PreflightSkip``.
- Opt-in set + anything else missing → ``PreflightFailure``.
- All checks pass → returns a populated ``PreflightConfig``.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from tests.e2e.preflight import (
    PreflightConfig,
    PreflightFailure,
    PreflightSkip,
    run_preflight,
)

# ---------------------------------------------------------------------------
# Fake CmdRunner — keyed by argv prefix
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Call:
    args: list[str]
    cwd: Path | None


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
        def handler(_args: list[str]) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(
                args=_args, returncode=returncode, stdout=stdout, stderr=stderr
            )

        self._handlers[prefix] = handler

    def __call__(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        check: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(_Call(args=list(args), cwd=cwd))
        best: tuple[str, ...] | None = None
        for prefix in self._handlers:
            if tuple(args[: len(prefix)]) == prefix:
                if best is None or len(prefix) > len(best):
                    best = prefix
        if best is None:
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")
        result = self._handlers[best](args)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, args, output=result.stdout, stderr=result.stderr
            )
        return result


def _good_runner() -> FakeRunner:
    """Runner pre-loaded with every check returning success."""
    runner = FakeRunner()
    runner.expect(("git", "--version"), stdout="git version 2.40.0")
    runner.expect(("gh", "auth", "status"), stdout="Logged in")
    runner.expect(("gh", "api", "user"), stdout="me")
    runner.expect(("gh", "repo", "view"), stdout="exists")
    runner.expect(
        ("claude", "--help"),
        stdout="--output-format <fmt>\n--print\n--verbose\n--settings",
    )
    runner.expect(("python3", "-c"), stdout="ok")
    runner.expect(("curl",), stdout="")
    return runner


def _good_env() -> Mapping[str, str]:
    return {
        "HAMMOCK_E2E_REAL_CLAUDE": "1",
        "HAMMOCK_E2E_TEST_REPO_URL": "https://github.com/me/e2e-test",
        "HAMMOCK_E2E_JOB_TYPE": "fix-bug",
    }


# ---------------------------------------------------------------------------
# Skip path
# ---------------------------------------------------------------------------


def test_opt_in_unset_skips() -> None:
    with pytest.raises(PreflightSkip, match="opt-in"):
        run_preflight(env={}, runner=_good_runner())


def test_opt_in_explicitly_zero_skips() -> None:
    with pytest.raises(PreflightSkip):
        run_preflight(env={"HAMMOCK_E2E_REAL_CLAUDE": "0"}, runner=_good_runner())


# ---------------------------------------------------------------------------
# Fail path — once opt-in is on, every miss is a hard fail
# ---------------------------------------------------------------------------


def test_missing_job_type_fails() -> None:
    env = dict(_good_env())
    del env["HAMMOCK_E2E_JOB_TYPE"]
    with pytest.raises(PreflightFailure, match="HAMMOCK_E2E_JOB_TYPE"):
        run_preflight(env=env, runner=_good_runner())


def test_missing_git_fails() -> None:
    runner = _good_runner()
    runner.expect(("git", "--version"), returncode=127)
    with pytest.raises(PreflightFailure, match="git not installed"):
        run_preflight(env=_good_env(), runner=runner)


def test_unauthenticated_gh_fails() -> None:
    runner = _good_runner()
    runner.expect(("gh", "auth", "status"), returncode=1, stderr="not logged in")
    with pytest.raises(PreflightFailure, match="gh CLI not authenticated"):
        run_preflight(env=_good_env(), runner=runner)


def test_gh_repo_not_found_is_NOT_a_preflight_failure() -> None:
    """The bootstrap step handles "repo not found"; preflight only
    fails on auth/network errors from gh repo view."""
    runner = _good_runner()
    runner.expect(
        ("gh", "repo", "view"),
        returncode=1,
        stderr="HTTP 404: Could not resolve to a Repository",
    )
    cfg = run_preflight(env=_good_env(), runner=runner)
    assert isinstance(cfg, PreflightConfig)


def test_gh_repo_auth_failure_fails_preflight() -> None:
    runner = _good_runner()
    runner.expect(("gh", "repo", "view"), returncode=1, stderr="HTTP 401: Bad credentials")
    with pytest.raises(PreflightFailure, match="test repo not viewable"):
        run_preflight(env=_good_env(), runner=runner)


def test_missing_claude_binary_fails() -> None:
    runner = _good_runner()
    runner.expect(("claude", "--help"), returncode=127)
    with pytest.raises(PreflightFailure, match="claude CLI not found"):
        run_preflight(env=_good_env(), runner=runner)


def test_claude_missing_required_flag_fails() -> None:
    runner = _good_runner()
    # Claude that doesn't list --output-format in --help.
    runner.expect(("claude", "--help"), stdout="some unrelated help")
    with pytest.raises(PreflightFailure, match="claude CLI flag support"):
        run_preflight(env=_good_env(), runner=runner)


def test_mcp_module_unimportable_fails() -> None:
    runner = _good_runner()
    runner.expect(("python3", "-c"), returncode=1, stderr="ModuleNotFoundError")
    with pytest.raises(PreflightFailure, match="MCP server module"):
        run_preflight(env=_good_env(), runner=runner)


def test_network_unreachable_fails() -> None:
    runner = _good_runner()
    runner.expect(("curl",), returncode=6)
    with pytest.raises(PreflightFailure, match="network unreachable"):
        run_preflight(env=_good_env(), runner=runner)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_returns_populated_config() -> None:
    cfg = run_preflight(env=_good_env(), runner=_good_runner())
    assert cfg.repo_url == "https://github.com/me/e2e-test"
    assert cfg.job_type == "fix-bug"
    assert cfg.keep_root is False
    assert cfg.timeout_min == 30


def test_keep_root_env_recognised() -> None:
    env = dict(_good_env())
    env["HAMMOCK_E2E_KEEP_ROOT"] = "1"
    cfg = run_preflight(env=env, runner=_good_runner())
    assert cfg.keep_root is True


def test_timeout_min_env_overrides_default() -> None:
    env = dict(_good_env())
    env["HAMMOCK_E2E_TIMEOUT_MIN"] = "45"
    cfg = run_preflight(env=env, runner=_good_runner())
    assert cfg.timeout_min == 45


def test_repo_url_derived_when_unset() -> None:
    """If HAMMOCK_E2E_TEST_REPO_URL is unset, derive
    ``https://github.com/<gh-user>/hammock-e2e-test`` from
    ``gh api user --jq .login``."""
    env = dict(_good_env())
    del env["HAMMOCK_E2E_TEST_REPO_URL"]
    runner = _good_runner()
    runner.expect(("gh", "api", "user"), stdout="myuser\n")
    cfg = run_preflight(env=env, runner=runner)
    assert cfg.repo_url == "https://github.com/myuser/hammock-e2e-test"


def test_claude_binary_override_recognised() -> None:
    env = dict(_good_env())
    env["HAMMOCK_CLAUDE_BINARY"] = "/opt/claude/bin/claude"
    runner = _good_runner()
    # The override path must be the one preflight checks.
    runner.expect(("/opt/claude/bin/claude", "--help"), stdout="--output-format")
    cfg = run_preflight(env=env, runner=runner)
    assert cfg.claude_binary == "/opt/claude/bin/claude"
