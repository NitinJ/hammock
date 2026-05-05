"""Preflight checks for e2e_v1.

Skips test if opt-in env var unset; fails if opt-in set but config is bad.
Resolves the test repo URL (default `https://github.com/<gh-user>/hammock-e2e-test`)
and confirms claude/gh are available.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

CmdRunner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(
    args: list[str], *, cwd: Path | None = None, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=check,
    )


class PreflightSkip(Exception):
    """Opt-in env var unset — test does not apply to this environment."""


class PreflightFailure(Exception):
    """Opt-in set but a precondition is missing — operator config bug."""


@dataclass(frozen=True)
class PreflightConfig:
    repo_url: str
    claude_binary: str
    keep_root: bool
    timeout_min: int


_REPO_NOT_FOUND = ("Could not resolve to a Repository", "HTTP 404")


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip() in {"1", "true", "True", "yes"}


def run_preflight(*, env: Mapping[str, str], runner: CmdRunner | None = None) -> PreflightConfig:
    runner = runner or _default_runner

    if not _is_truthy(env.get("HAMMOCK_E2E_REAL_CLAUDE")):
        raise PreflightSkip("opt-in env HAMMOCK_E2E_REAL_CLAUDE unset")

    if runner(["git", "--version"]).returncode != 0:
        raise PreflightFailure("git not on PATH")
    if runner(["gh", "auth", "status"]).returncode != 0:
        raise PreflightFailure("gh CLI not authenticated (run `gh auth login`)")

    repo_url = env.get("HAMMOCK_E2E_TEST_REPO_URL")
    if not repo_url:
        user_result = runner(["gh", "api", "user", "--jq", ".login"])
        if user_result.returncode != 0 or not user_result.stdout.strip():
            raise PreflightFailure("could not derive default repo URL: gh api user failed")
        repo_url = f"https://github.com/{user_result.stdout.strip()}/hammock-e2e-test"

    slug = _slug_from_url(repo_url)
    view = runner(["gh", "repo", "view", slug])
    if view.returncode != 0 and not _is_repo_not_found(view.stderr):
        raise PreflightFailure(
            f"test repo not viewable (auth/network/other): {view.stderr.strip()}"
        )

    claude_binary = env.get("HAMMOCK_CLAUDE_BINARY", "claude")
    claude_help = runner([claude_binary, "--help"])
    if claude_help.returncode != 0:
        raise PreflightFailure(f"claude CLI not runnable at {claude_binary!r}")
    if "--output-format" not in claude_help.stdout:
        raise PreflightFailure("claude CLI missing --output-format flag")

    if (
        runner(["curl", "-fsS", "https://api.github.com", "-o", "/dev/null", "-m", "5"]).returncode
        != 0
    ):
        raise PreflightFailure("network unreachable (api.github.com probe failed)")

    timeout_raw = env.get("HAMMOCK_E2E_TIMEOUT_MIN", "30")
    try:
        timeout_min = int(timeout_raw)
        if timeout_min <= 0:
            raise ValueError
    except ValueError as exc:
        raise PreflightFailure(
            f"HAMMOCK_E2E_TIMEOUT_MIN must be a positive int, got {timeout_raw!r}"
        ) from exc

    return PreflightConfig(
        repo_url=repo_url,
        claude_binary=claude_binary,
        keep_root=_is_truthy(env.get("HAMMOCK_E2E_KEEP_ROOT")),
        timeout_min=timeout_min,
    )


def _is_repo_not_found(stderr: str) -> bool:
    return any(frag in stderr for frag in _REPO_NOT_FOUND)


def _slug_from_url(repo_url: str) -> str:
    if "://" not in repo_url and repo_url.count("/") == 1:
        return repo_url
    from urllib.parse import urlparse

    path = urlparse(repo_url).path.lstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return path
