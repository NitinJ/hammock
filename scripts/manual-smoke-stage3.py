"""Stage 3 manual smoke.

Drives ``hammock job submit`` end-to-end: register a project, submit a
build-feature job, then submit a fix-bug job, then submit one with
deliberate compile failures to assert structured errors.

Run with::

    uv run python scripts/manual-smoke-stage3.py
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


def _hammock(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["uv", "run", "hammock", *args],
        cwd=str(REPO_ROOT),
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=False,
    )


def _expect_ok(res: subprocess.CompletedProcess[str], label: str) -> None:
    if res.returncode != 0:
        print(f"  ✗ {label} (exit {res.returncode})")
        print(f"    stdout: {res.stdout[:600]}")
        print(f"    stderr: {res.stderr[:600]}")
        raise SystemExit(1)
    print(f"  ✓ {label}")


def _expect_fail(res: subprocess.CompletedProcess[str], label: str) -> None:
    if res.returncode == 0:
        print(f"  ✗ {label} expected failure but exited 0")
        print(f"    stdout: {res.stdout[:600]}")
        raise SystemExit(1)
    print(f"  ✓ {label}")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="hammock-stage3-") as tmp:
        root = Path(tmp) / "hammock-root"
        root.mkdir()
        repo = Path(tmp) / "Smoke-Repo-Stage3"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/example/smoke3.git"],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
        env = {"HAMMOCK_ROOT": str(root)}
        print(f"smoke root: {root}")
        print(f"smoke repo: {repo}")

        # 1. register
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
        slug = "smoke-repo-stage3"

        # 2. dry-run a build-feature job
        res = _hammock(
            env,
            "job",
            "submit",
            "--project",
            slug,
            "--type",
            "build-feature",
            "--title",
            "add invite onboarding",
            "--request-text",
            "Build invite-only onboarding.",
            "--dry-run",
            "--json",
        )
        _expect_ok(res, "submit build-feature --dry-run")
        data = json.loads(res.stdout)
        assert data["dry_run"] is True
        assert data["stage_count"] >= 12
        assert not (root / "jobs" / data["job_slug"]).exists()

        # 3. real build-feature submit
        res = _hammock(
            env,
            "job",
            "submit",
            "--project",
            slug,
            "--type",
            "build-feature",
            "--title",
            "feature thing",
            "--request-text",
            "Build a thing.",
            "--json",
        )
        _expect_ok(res, "submit build-feature (writes job dir)")
        bf_slug = json.loads(res.stdout)["job_slug"]
        assert (root / "jobs" / bf_slug / "job.json").exists()
        assert (root / "jobs" / bf_slug / "prompt.md").exists()
        assert (root / "jobs" / bf_slug / "stage-list.yaml").exists()

        # 4. fix-bug submit
        res = _hammock(
            env,
            "job",
            "submit",
            "--project",
            slug,
            "--type",
            "fix-bug",
            "--title",
            "login redirect loop",
            "--request-text",
            "Bug: login bounces.",
            "--json",
        )
        _expect_ok(res, "submit fix-bug")
        fb_slug = json.loads(res.stdout)["job_slug"]
        assert (root / "jobs" / fb_slug / "job.json").exists()

        # 5. deliberate failure: unknown project
        res = _hammock(
            env,
            "job",
            "submit",
            "--project",
            "no-such-project",
            "--type",
            "build-feature",
            "--title",
            "x",
            "--request-text",
            "y",
            "--json",
        )
        _expect_fail(res, "submit unknown-project (structured failure)")
        data = json.loads(res.stdout)
        assert data["ok"] is False
        assert any(f["kind"] == "project_not_found" for f in data["failures"])

        # 6. deliberate failure: bogus override
        repo_overrides = repo / ".hammock" / "job-template-overrides"
        (repo_overrides / "build-feature.yaml").write_text(
            "stages:\n  - id: brand-new-stage-not-in-base\n    description: bad\n"
        )
        res = _hammock(
            env,
            "job",
            "submit",
            "--project",
            slug,
            "--type",
            "build-feature",
            "--title",
            "should fail",
            "--request-text",
            "x",
            "--json",
        )
        _expect_fail(res, "submit with bogus override")
        data = json.loads(res.stdout)
        assert any("override" in f["kind"] for f in data["failures"])

        print("\nsmoke OK: 6 invocations exercised end-to-end")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
