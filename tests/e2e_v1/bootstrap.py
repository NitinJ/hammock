"""Bootstrap helper for the e2e_v1 test repo.

Idempotent: if the repo exists, returns it as-is; if it doesn't, creates
it and pushes the seed contents. Per the design's reuse-the-same-repo
discipline (only one ``hammock-e2e-test`` repo per gh user, never v1/v2/v3
spam), this helper never deletes a repo or rewrites its history.

The single seed lives at ``tests/e2e_v1/seed_test_repo/`` and contains a
small Python project with a known bug. Used as the repo's initial main
branch when the repo is created for the first time.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

CmdRunner = Callable[..., subprocess.CompletedProcess[str]]


_GH_NOT_FOUND = (
    "Could not resolve to a Repository",
    "HTTP 404",
    "GraphQL: Could not resolve",
)


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


class RepoBootstrapError(Exception):
    """Raised when bootstrap can't proceed (auth denied, missing seed, or
    any non-not-found ``gh repo view`` failure)."""


@dataclass(frozen=True)
class BootstrapResult:
    created: bool
    repo_url: str
    repo_slug: str  # ``owner/repo``


def _slug_from_url(repo_url: str) -> str:
    if "://" not in repo_url and repo_url.count("/") == 1:
        return repo_url
    path = urlparse(repo_url).path.lstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    if path.count("/") != 1:
        raise RepoBootstrapError(f"could not parse owner/repo from {repo_url!r}")
    return path


def _is_not_found(stderr: str) -> bool:
    return any(frag in stderr for frag in _GH_NOT_FOUND)


def bootstrap_test_repo(
    repo_url: str,
    *,
    seed_dir: Path,
    runner: CmdRunner | None = None,
) -> BootstrapResult:
    """Reuse-or-create the test repo.

    - ``gh repo view`` succeeds → reuse as-is.
    - ``gh repo view`` fails with a not-found stderr → create the repo
      via ``gh repo create --private``, clone, copy seed contents, push
      the seed to ``main``.
    - Any other ``gh repo view`` failure → raise.
    """
    if runner is None:
        runner = _default_runner
    if not seed_dir.is_dir():
        raise RepoBootstrapError(f"seed_dir does not exist: {seed_dir}")

    slug = _slug_from_url(repo_url)
    view = runner(["gh", "repo", "view", slug])
    if view.returncode == 0:
        return BootstrapResult(created=False, repo_url=repo_url, repo_slug=slug)
    if not _is_not_found(view.stderr):
        raise RepoBootstrapError(
            f"gh repo view {slug!r} failed (auth/network/other): "
            f"rc={view.returncode} stderr={view.stderr!r}"
        )

    # ----- create + seed -------------------------------------------------
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

    with tempfile.TemporaryDirectory(prefix="hammock-e2e-v1-bootstrap-") as tmp:
        clone_dir = Path(tmp) / "repo"
        runner(
            ["git", "clone", f"https://github.com/{slug}.git", str(clone_dir)],
            check=True,
        )
        # Force `main` regardless of the gh user's default branch setting.
        runner(["git", "checkout", "-B", "main"], cwd=clone_dir, check=True)
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
            ["git", "commit", "-m", "seed: hammock e2e v1 bootstrap"],
            cwd=clone_dir,
            check=True,
        )
        runner(["git", "push", "-u", "origin", "main"], cwd=clone_dir, check=True)

    return BootstrapResult(created=True, repo_url=repo_url, repo_slug=slug)
