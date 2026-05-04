"""Tests for ``tests.e2e.repo_bootstrap``.

Per docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step C:
the bootstrap helper either creates the test repo (with seed push +
branch protection) or reuses an existing one. Tests inject a fake
command runner so we never call real ``gh`` / ``git`` here.
"""

from __future__ import annotations

import dataclasses
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.e2e.repo_bootstrap import (
    GH_NOT_FOUND_FRAGMENTS,
    RepoBootstrapError,
    RepoBootstrapResult,
    bootstrap_test_repo,
)

# ---------------------------------------------------------------------------
# Fake CmdRunner — records calls + dispatches by-prefix
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _FakeCall:
    args: list[str]
    cwd: Path | None


class FakeRunner:
    """Records calls; returns canned CompletedProcess by-prefix.

    Each handler is keyed by a tuple prefix of ``args``; the longest
    matching prefix wins. Default handler returns a successful empty
    CompletedProcess. Tests register handlers per-scenario.
    """

    def __init__(self) -> None:
        self.calls: list[_FakeCall] = []
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
        self.calls.append(_FakeCall(args=list(args), cwd=cwd))
        # Find the longest matching prefix.
        best: tuple[str, ...] | None = None
        for prefix in self._handlers:
            if tuple(args[: len(prefix)]) == prefix:
                if best is None or len(prefix) > len(best):
                    best = prefix
        if best is None:
            result = subprocess.CompletedProcess(
                args=list(args), returncode=0, stdout="", stderr=""
            )
        else:
            result = self._handlers[best](args)
        # Mimic enough of `git clone <url> <path>` for downstream
        # filesystem ops in the bootstrap flow — but only when the
        # call would have succeeded. Codex review on PR #29: a future
        # failing-clone test shouldn't get the dir created behind it.
        if args[:2] == ["git", "clone"] and len(args) >= 4 and result.returncode == 0:
            Path(args[3]).mkdir(parents=True, exist_ok=True)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, args, output=result.stdout, stderr=result.stderr
            )
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def seed_dir(tmp_path: Path) -> Path:
    d = tmp_path / "seed"
    d.mkdir()
    (d / "README.md").write_text("hi\n")
    return d


def _gh_args_have(runner: FakeRunner, *fragments: str) -> bool:
    """True if at least one recorded call has *all* fragments somewhere
    in its argv."""
    for call in runner.calls:
        joined = " ".join(call.args)
        if all(frag in joined for frag in fragments):
            return True
    return False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_bootstrap_reuses_when_present(seed_dir: Path) -> None:
    """``gh repo view`` succeeds → no create / push / protection calls."""
    runner = FakeRunner()
    runner.expect(("gh", "repo", "view"), returncode=0, stdout="exists")

    result = bootstrap_test_repo("https://github.com/me/e2e-test", seed_dir=seed_dir, runner=runner)

    assert isinstance(result, RepoBootstrapResult)
    assert result.created is False
    assert not _gh_args_have(runner, "repo", "create")
    assert not _gh_args_have(runner, "git", "push")
    assert not _gh_args_have(runner, "branches", "main", "protection")


def test_bootstrap_creates_when_absent_then_seeds_then_protects(
    seed_dir: Path,
) -> None:
    """``gh repo view`` returns a 'not found' stderr → create, clone,
    seed-push, and enable branch protection."""
    runner = FakeRunner()
    runner.expect(
        ("gh", "repo", "view"),
        returncode=1,
        stderr=GH_NOT_FOUND_FRAGMENTS[0],
    )

    result = bootstrap_test_repo("https://github.com/me/e2e-test", seed_dir=seed_dir, runner=runner)

    assert result.created is True
    assert _gh_args_have(runner, "repo", "create", "--private")
    assert _gh_args_have(runner, "git", "clone")
    # Codex review on PR #29: must force ``main`` regardless of
    # remote-side default-branch configuration.
    assert _gh_args_have(runner, "checkout", "-B", "main")
    assert _gh_args_have(runner, "git", "push")
    assert _gh_args_have(runner, "branches/main/protection")


def test_bootstrap_seed_push_targets_main_only(seed_dir: Path) -> None:
    """The seed push must go to ``main`` and only to ``main``."""
    runner = FakeRunner()
    runner.expect(
        ("gh", "repo", "view"),
        returncode=1,
        stderr="HTTP 404: Could not resolve to a Repository",
    )

    bootstrap_test_repo("https://github.com/me/e2e-test", seed_dir=seed_dir, runner=runner)

    push_calls = [c for c in runner.calls if c.args[:2] == ["git", "push"]]
    assert len(push_calls) == 1
    assert "main" in push_calls[0].args


def test_bootstrap_protection_payload_pins_review_count(seed_dir: Path) -> None:
    """Branch-protection PUT must require >=1 approving review (parity
    with production hammock workflows)."""
    runner = FakeRunner()
    runner.expect(
        ("gh", "repo", "view"),
        returncode=1,
        stderr="Could not resolve to a Repository",
    )

    bootstrap_test_repo("https://github.com/me/e2e-test", seed_dir=seed_dir, runner=runner)

    protection_calls = [c for c in runner.calls if "branches/main/protection" in " ".join(c.args)]
    assert len(protection_calls) == 1
    payload = " ".join(protection_calls[0].args)
    # Bracket-nested form per gh api typed-flag (-F) convention.
    assert "required_pull_request_reviews[required_approving_review_count]=1" in payload
    # Typed booleans/nulls must use -F so gh emits proper JSON, not strings.
    assert "-F" in protection_calls[0].args


def test_bootstrap_soft_fails_on_protection_403(
    seed_dir: Path, caplog: object
) -> None:
    """GitHub free tier rejects protection on private repos with HTTP 403
    ("Upgrade to GitHub Pro or make this repository public"). Bootstrap
    must log a warning and continue rather than crashing the test —
    protection is cosmetic for correctness (gates are stitched
    programmatically; the test never auto-merges)."""
    import logging as _logging

    import pytest as _pytest

    runner = FakeRunner()
    runner.expect(
        ("gh", "repo", "view"),
        returncode=1,
        stderr="Could not resolve to a Repository",
    )
    runner.expect(
        ("gh", "api", "-X", "PUT"),
        returncode=1,
        stderr="gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)",
    )

    assert isinstance(caplog, _pytest.LogCaptureFixture)
    with caplog.at_level(_logging.WARNING, logger="tests.e2e.repo_bootstrap"):
        result = bootstrap_test_repo(
            "https://github.com/me/e2e-test", seed_dir=seed_dir, runner=runner
        )

    # Bootstrap completed despite the 403.
    assert result.created is True
    # And it logged the limitation so operators see why protection isn't on.
    assert any(
        "protection" in r.getMessage().lower() for r in caplog.records
    )


def test_bootstrap_raises_on_auth_error(seed_dir: Path) -> None:
    """gh repo view returning a non-not-found error → raise instead of
    auto-creating (otherwise we'd mask auth/network problems)."""
    runner = FakeRunner()
    runner.expect(
        ("gh", "repo", "view"),
        returncode=1,
        stderr="HTTP 401: Bad credentials",
    )

    with pytest.raises(RepoBootstrapError, match="auth"):
        bootstrap_test_repo("https://github.com/me/e2e-test", seed_dir=seed_dir, runner=runner)


def test_bootstrap_normalises_repo_url_for_gh_calls(seed_dir: Path) -> None:
    """Whether the operator passes ``owner/repo`` or the full
    ``https://github.com/owner/repo``, ``gh`` calls reference
    ``owner/repo``."""
    runner = FakeRunner()
    runner.expect(("gh", "repo", "view"), returncode=0)

    bootstrap_test_repo("https://github.com/me/e2e-test", seed_dir=seed_dir, runner=runner)

    view_call = next(c for c in runner.calls if c.args[:3] == ["gh", "repo", "view"])
    assert view_call.args[3] == "me/e2e-test"


def test_bootstrap_seed_dir_must_exist(tmp_path: Path) -> None:
    runner = FakeRunner()
    runner.expect(("gh", "repo", "view"), returncode=0)

    with pytest.raises(RepoBootstrapError, match="seed"):
        bootstrap_test_repo(
            "https://github.com/me/e2e-test",
            seed_dir=tmp_path / "missing",
            runner=runner,
        )
