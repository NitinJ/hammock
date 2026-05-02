"""Stage 2 manual smoke.

Drives the seven ``hammock project ...`` verbs against a real (temporary)
hammock root and a fake repo. Skips the gh remote checks so the smoke runs
without network or gh-auth assumptions.

Run with::

    uv run python scripts/manual-smoke-stage2.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _hammock(
    env: dict[str, str], *args: str, input: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Invoke ``uv run hammock ...`` in *env*."""
    return subprocess.run(
        ["uv", "run", "hammock", *args],
        cwd=str(REPO_ROOT),
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        input=input,
        check=False,
    )


def _expect_ok(res: subprocess.CompletedProcess[str], label: str) -> None:
    if res.returncode != 0:
        print(f"  ✗ {label} (exit {res.returncode})")
        print(f"    stdout: {res.stdout[:400]}")
        print(f"    stderr: {res.stderr[:400]}")
        raise SystemExit(1)
    print(f"  ✓ {label}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hammock-stage2-") as tmp:
        root = Path(tmp) / "hammock-root"
        root.mkdir()
        repo = Path(tmp) / "MyRepo-2026"
        repo.mkdir()
        (repo / ".git").mkdir()  # minimum to look like a git repo

        # Configure a fake git remote so 'register' has something to find.
        subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/example/smoke-repo.git"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", "main"],
            cwd=str(repo),
            capture_output=True,
        )

        env = {"HAMMOCK_ROOT": str(root)}
        print(f"smoke root: {root}")
        print(f"smoke repo: {repo}")

        # 1. register (skip remote checks; we don't have a real GitHub repo).
        # Pass --default-branch since our fake repo has no commits yet so
        # branch detection fails.
        res = _hammock(
            env,
            "project",
            "register",
            str(repo),
            "--skip-remote-checks",
            "--default-branch",
            "main",
        )
        _expect_ok(res, "register")
        assert (root / "projects" / "myrepo-2026" / "project.json").exists()
        assert (repo / ".hammock" / "agent-overrides").is_dir()
        assert ".hammock/" in (repo / ".gitignore").read_text()

        # 2. list (default + --json)
        res = _hammock(env, "project", "list")
        _expect_ok(res, "list (table)")
        assert "myrepo-2026" in res.stdout

        res = _hammock(env, "project", "list", "--json")
        _expect_ok(res, "list --json")
        data = json.loads(res.stdout)
        assert data[0]["slug"] == "myrepo-2026"

        # 3. show
        res = _hammock(env, "project", "show", "myrepo-2026", "--json")
        _expect_ok(res, "show --json")
        assert json.loads(res.stdout)["slug"] == "myrepo-2026"

        # 4. rename
        res = _hammock(env, "project", "rename", "myrepo-2026", "Friendly Name")
        _expect_ok(res, "rename")
        renamed = json.loads((root / "projects" / "myrepo-2026" / "project.json").read_text())
        assert renamed["name"] == "Friendly Name"
        assert renamed["slug"] == "myrepo-2026"

        # 5. doctor (--yes auto-fixes; --json for parseable output)
        res = _hammock(env, "project", "doctor", "myrepo-2026", "--yes", "--json")
        _expect_ok(res, "doctor --json")
        report = json.loads(res.stdout)
        assert len(report["checks"]) == 12
        assert report["status"] in {"pass", "warn", "fail"}

        # 6. relocate (move to new location)
        new_path = Path(tmp) / "MyRepo-2026-moved"
        new_path.mkdir()
        (new_path / ".git").mkdir()
        subprocess.run(["git", "init"], cwd=str(new_path), check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/example/smoke-repo.git"],
            cwd=str(new_path),
            check=True,
            capture_output=True,
        )
        # relocate verifies the remote_url matches, which the new fake repo
        # configures the same way; --force not required.
        res = _hammock(env, "project", "relocate", "myrepo-2026", str(new_path))
        _expect_ok(res, "relocate")
        relocated = json.loads((root / "projects" / "myrepo-2026" / "project.json").read_text())
        assert relocated["repo_path"] == str(new_path)

        # 7. deregister
        res = _hammock(env, "project", "deregister", "myrepo-2026", "--yes")
        _expect_ok(res, "deregister")
        assert not (root / "projects" / "myrepo-2026").exists()

        print("\nsmoke OK: 7 verbs exercised end-to-end")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
