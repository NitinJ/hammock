"""Preflight checks for the real-claude e2e test.

Per docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step D and
spec D12: opt-in unset → :class:`PreflightSkip` (the test simply
doesn't apply to this environment); opt-in set + anything else
missing → :class:`PreflightFailure` (operator opted in but the
config is wrong; surface the bug rather than silently dropping the
test).

The :func:`run_preflight` function is pure (no module-level state).
A fixture wraps it in the test harness, translating
``PreflightSkip`` to ``pytest.skip`` and ``PreflightFailure`` to
``pytest.fail``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

CmdRunner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=check,
    )


class PreflightSkip(Exception):
    """The test doesn't apply to this environment (opt-in not set)."""


class PreflightFailure(Exception):
    """Opt-in set but a precondition failed — operator config bug."""


@dataclass(frozen=True)
class PreflightConfig:
    repo_url: str
    job_type: str
    claude_binary: str
    keep_root: bool
    timeout_min: int


# stderr fragments that mean "the repo doesn't exist yet" — bootstrap
# handles that case, so preflight must NOT fail on it.
_REPO_NOT_FOUND_FRAGMENTS: tuple[str, ...] = (
    "Could not resolve to a Repository",
    "HTTP 404",
)


def _is_truthy(value: str | None) -> bool:
    return value is not None and value.strip() in {"1", "true", "True", "yes"}


def run_preflight(
    *,
    env: Mapping[str, str],
    runner: CmdRunner | None = None,
) -> PreflightConfig:
    """Run every preflight check and return the populated config.

    Raises :class:`PreflightSkip` when the opt-in env var is unset
    (the only condition that means "don't run the test"); raises
    :class:`PreflightFailure` for everything else.
    """
    if runner is None:
        runner = _default_runner

    if not _is_truthy(env.get("HAMMOCK_E2E_REAL_CLAUDE")):
        raise PreflightSkip("opt-in env var HAMMOCK_E2E_REAL_CLAUDE not set")

    job_type = env.get("HAMMOCK_E2E_JOB_TYPE")
    if not job_type:
        raise PreflightFailure("opt-in set but HAMMOCK_E2E_JOB_TYPE missing")

    # --- tooling ----------------------------------------------------
    if runner(["git", "--version"]).returncode != 0:
        raise PreflightFailure("git not installed or not on $PATH")

    if runner(["gh", "auth", "status"]).returncode != 0:
        raise PreflightFailure("gh CLI not authenticated (run `gh auth login`)")

    # Resolve repo URL — explicit env var, else derive from the
    # authenticated gh user (per spec §Test repo, default
    # ``https://github.com/<gh-user>/hammock-e2e-test``).
    repo_url = env.get("HAMMOCK_E2E_TEST_REPO_URL")
    if not repo_url:
        user_result = runner(["gh", "api", "user", "--jq", ".login"])
        if user_result.returncode != 0 or not user_result.stdout.strip():
            raise PreflightFailure("could not derive default repo URL: gh api user failed")
        repo_url = f"https://github.com/{user_result.stdout.strip()}/hammock-e2e-test"

    # gh repo view check — "not found" is fine (bootstrap will create);
    # anything else (auth/network) is a preflight failure.
    slug = _slug_from_url(repo_url)
    view = runner(["gh", "repo", "view", slug])
    if view.returncode != 0 and not _is_repo_not_found(view.stderr):
        raise PreflightFailure(
            f"test repo not viewable by gh (auth/network/other): {view.stderr.strip()}"
        )

    # --- claude binary ---------------------------------------------
    claude_binary = env.get("HAMMOCK_CLAUDE_BINARY", "claude")
    claude_help = runner([claude_binary, "--help"])
    if claude_help.returncode != 0:
        raise PreflightFailure(f"claude CLI not found or unrunnable at {claude_binary!r}")
    if "--output-format" not in claude_help.stdout:
        raise PreflightFailure(
            "claude CLI flag support insufficient: --output-format missing from --help"
        )

    # --- MCP module importable under the same interpreter ----------
    if runner(["python3", "-c", "import dashboard.mcp"]).returncode != 0:
        raise PreflightFailure(
            "MCP server module not importable (dashboard.mcp); is the editable install present?"
        )

    # --- network probe ---------------------------------------------
    if (
        runner(["curl", "-fsS", "https://api.github.com", "-o", "/dev/null", "-m", "5"]).returncode
        != 0
    ):
        raise PreflightFailure("network unreachable (api.github.com probe failed)")

    # --- assemble config -------------------------------------------
    timeout_raw = env.get("HAMMOCK_E2E_TIMEOUT_MIN", "30")
    try:
        timeout_min = int(timeout_raw)
        if timeout_min <= 0:
            raise ValueError
    except ValueError as exc:
        raise PreflightFailure(
            f"HAMMOCK_E2E_TIMEOUT_MIN must be a positive integer, got {timeout_raw!r}"
        ) from exc

    return PreflightConfig(
        repo_url=repo_url,
        job_type=job_type,
        claude_binary=claude_binary,
        keep_root=_is_truthy(env.get("HAMMOCK_E2E_KEEP_ROOT")),
        timeout_min=timeout_min,
    )


def _is_repo_not_found(stderr: str) -> bool:
    return any(frag in stderr for frag in _REPO_NOT_FOUND_FRAGMENTS)


def _slug_from_url(repo_url: str) -> str:
    if "://" not in repo_url and repo_url.count("/") == 1:
        return repo_url
    from urllib.parse import urlparse

    path = urlparse(repo_url).path.lstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    return path
