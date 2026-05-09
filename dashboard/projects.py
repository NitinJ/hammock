"""Project storage + health helpers for v2.

A project is a registered local git checkout. We persist a small json
under ``<HAMMOCK_ROOT>/projects/<slug>.json``. Registration is
operator-driven and never touches the working tree. Health is computed
on read by checking the path exists and contains ``.git/``.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
from pathlib import Path
from typing import Any

# Slugs become path segments. Be strict.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SLUG_NORMALIZE = re.compile(r"[^a-z0-9._-]+")


class ProjectError(Exception):
    """Bad input or invalid project state."""


def projects_dir(root: Path) -> Path:
    return root / "projects"


def project_json_path(slug: str, root: Path) -> Path:
    return projects_dir(root) / f"{slug}.json"


def normalize_slug(raw: str) -> str:
    """Coerce an arbitrary string to a slug. Empty result raises."""
    s = raw.strip().lower()
    s = _SLUG_NORMALIZE.sub("-", s).strip("-_.")
    s = s[:64]
    if not s:
        raise ProjectError("slug cannot be empty after normalization")
    if not _SLUG_RE.match(s):
        raise ProjectError(f"slug must match {_SLUG_RE.pattern!r} after normalization (got {s!r})")
    return s


def derive_slug_from_path(repo_path: Path) -> str:
    """Slug = basename of the repo path, normalized."""
    return normalize_slug(repo_path.name)


def health_check(repo_path: Path) -> dict[str, Any]:
    """Compute health: path_exists, is_git_repo, default_branch."""
    out: dict[str, Any] = {
        "path_exists": False,
        "is_git_repo": False,
        "default_branch": None,
    }
    if not repo_path.is_dir():
        return out
    out["path_exists"] = True
    if not (repo_path / ".git").exists():
        return out
    out["is_git_repo"] = True
    # Probe default branch via git symbolic-ref. Fall back gracefully.
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            ref = result.stdout.strip()
            if "/" in ref:
                out["default_branch"] = ref.rsplit("/", 1)[-1]
    except (subprocess.SubprocessError, FileNotFoundError):
        pass
    if out["default_branch"] is None:
        # Fall back: check if main or master exists
        for candidate in ("main", "master"):
            if (repo_path / ".git" / "refs" / "heads" / candidate).is_file():
                out["default_branch"] = candidate
                break
    return out


def write_project(
    *,
    slug: str,
    repo_path: Path,
    name: str | None,
    root: Path,
    registered_at: str | None = None,
    default_branch: str | None = None,
) -> Path:
    """Write the project json. Returns the path."""
    slug = normalize_slug(slug)
    pdir = projects_dir(root)
    pdir.mkdir(parents=True, exist_ok=True)
    payload = {
        "slug": slug,
        "name": name or slug,
        "repo_path": str(repo_path),
        "registered_at": registered_at or _dt.datetime.now(_dt.UTC).isoformat(),
        "default_branch": default_branch,
    }
    target = project_json_path(slug, root)
    target.write_text(json.dumps(payload, indent=2) + "\n")
    return target


def read_project(slug: str, root: Path) -> dict[str, Any] | None:
    """Read the project json. Returns None if missing."""
    slug = normalize_slug(slug)
    target = project_json_path(slug, root)
    if not target.is_file():
        return None
    return json.loads(target.read_text())


def list_projects(root: Path) -> list[dict[str, Any]]:
    """List every registered project. Each entry has the persisted
    fields plus a ``health:`` subdict."""
    pdir = projects_dir(root)
    if not pdir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(pdir.glob("*.json")):
        try:
            data = json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        repo_path = Path(data.get("repo_path", ""))
        data["health"] = (
            health_check(repo_path)
            if str(repo_path)
            else {
                "path_exists": False,
                "is_git_repo": False,
                "default_branch": None,
            }
        )
        out.append(data)
    return out


def delete_project(slug: str, root: Path) -> bool:
    """Remove the project json. Returns True if a file was deleted."""
    slug = normalize_slug(slug)
    target = project_json_path(slug, root)
    if not target.is_file():
        return False
    target.unlink()
    return True


__all__ = [
    "ProjectError",
    "delete_project",
    "derive_slug_from_path",
    "health_check",
    "list_projects",
    "normalize_slug",
    "project_json_path",
    "projects_dir",
    "read_project",
    "write_project",
]
