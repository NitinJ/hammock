"""Bootstrap helper for the real-claude e2e test repo.

Per docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step C:

- ``gh repo view`` succeeds → reuse-as-is.
- ``gh repo view`` fails with a "not found" stderr fragment → create
  via ``gh repo create --private``, clone, copy ``seed_dir/*`` into
  the clone, push the seed to ``main``, then enable branch protection
  on ``main`` so the test repo mirrors production Hammock workflows.
- ``gh repo view`` fails with anything else (auth denied, network) →
  raise :class:`RepoBootstrapError` so a misconfigured environment
  doesn't get auto-provisioning.

The helper takes a ``runner`` callable so unit tests can inject a
fake without monkey-patching :mod:`subprocess`. The default runner is
:func:`subprocess.run` with ``capture_output=True, text=True``.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# stderr fragments that indicate "the repo simply doesn't exist yet" —
# anything else from ``gh repo view`` is a real error and surfaces as
# :class:`RepoBootstrapError`.
GH_NOT_FOUND_FRAGMENTS: tuple[str, ...] = (
    "Could not resolve to a Repository",
    "HTTP 404",
    "GraphQL: Could not resolve",
)


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


class RepoBootstrapError(Exception):
    """Raised when bootstrap can't proceed (auth denied, missing seed,
    or any non-not-found ``gh repo view`` failure)."""


@dataclass(frozen=True)
class RepoBootstrapResult:
    created: bool
    repo_url: str
    repo_slug: str  # ``owner/repo``
    default_branch: str  # always ``main`` in v0


def _normalise_repo_slug(repo_url: str) -> str:
    """Return the ``owner/repo`` form of *repo_url*.

    Accepts both full HTTPS URLs (``https://github.com/owner/repo``)
    and the slug form (``owner/repo``).
    """
    if "://" not in repo_url and repo_url.count("/") == 1:
        return repo_url
    parsed = urlparse(repo_url)
    path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    if path.count("/") != 1:
        raise RepoBootstrapError(f"could not parse owner/repo from {repo_url!r}")
    return path


def _is_not_found(stderr: str) -> bool:
    return any(frag in stderr for frag in GH_NOT_FOUND_FRAGMENTS)


def bootstrap_test_repo(
    repo_url: str,
    *,
    seed_dir: Path,
    runner: CmdRunner | None = None,
) -> RepoBootstrapResult:
    """Create-or-reuse the test repo per spec D18.

    Returns :class:`RepoBootstrapResult` describing the outcome.
    """
    if runner is None:
        runner = _default_runner

    if not seed_dir.is_dir():
        raise RepoBootstrapError(f"seed_dir does not exist: {seed_dir}")

    slug = _normalise_repo_slug(repo_url)

    view = runner(["gh", "repo", "view", slug])
    if view.returncode == 0:
        return RepoBootstrapResult(
            created=False, repo_url=repo_url, repo_slug=slug, default_branch="main"
        )

    if not _is_not_found(view.stderr):
        raise RepoBootstrapError(
            f"gh repo view {slug!r} failed (auth/network/other?): "
            f"rc={view.returncode} stderr={view.stderr!r}"
        )

    # --- create + seed + protect ------------------------------------
    runner(
        [
            "gh",
            "repo",
            "create",
            slug,
            "--private",
            "--description",
            "Hammock e2e test repo (auto-bootstrapped)",
        ],
        check=True,
    )

    with tempfile.TemporaryDirectory(prefix="hammock-e2e-bootstrap-") as tmp:
        clone_dir = Path(tmp) / "repo"
        # Use the slug form so SSH/HTTPS auth follows whichever ``gh``
        # already configured, instead of forcing a specific scheme.
        runner(["git", "clone", f"https://github.com/{slug}.git", str(clone_dir)], check=True)
        # Copy seed contents into the clone (skip .git inside seed if any).
        for item in seed_dir.iterdir():
            if item.name == ".git":
                continue
            dest = clone_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        runner(["git", "add", "."], cwd=clone_dir, check=True)
        runner(
            ["git", "commit", "-m", "seed: hammock e2e bootstrap"],
            cwd=clone_dir,
            check=True,
        )
        runner(["git", "push", "-u", "origin", "main"], cwd=clone_dir, check=True)

    # Branch protection — 1 approving review, no force-push, no admin
    # bypass (mirrors production Hammock workflows).
    runner(
        [
            "gh",
            "api",
            "-X",
            "PUT",
            f"repos/{slug}/branches/main/protection",
            "-f",
            "required_pull_request_reviews.required_approving_review_count=1",
            "-f",
            "enforce_admins=false",
            "-f",
            "restrictions=null",
            "-f",
            "required_status_checks=null",
            "-f",
            "allow_force_pushes=false",
        ],
        check=True,
    )

    return RepoBootstrapResult(
        created=True, repo_url=repo_url, repo_slug=slug, default_branch="main"
    )
