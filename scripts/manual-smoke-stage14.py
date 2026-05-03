#!/usr/bin/env python3
"""Manual smoke test for Stage 14 — Job submit + Plan Compiler integration.

Exercises POST /api/jobs against a live dashboard process. Requires:
  - Dashboard running: uv run python -m dashboard  (in background)
  - At least one project registered (see scripts/manual-smoke-stage2.py)

Usage:
    uv run python scripts/manual-smoke-stage14.py [--project-slug SLUG]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{BASE}{path}") as r:
        return json.loads(r.read())


def post(path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-slug", default=None)
    args = parser.parse_args()

    # --- pick a project slug ---
    project_slug = args.project_slug
    if project_slug is None:
        projects = get("/api/projects")
        if not projects:
            print("No projects registered. Run: hammock project add <path>")
            sys.exit(1)
        project_slug = projects[0]["slug"]
        print(f"Using project: {project_slug}")

    print("\n=== Test 1: dry-run ===")
    status, body = post(
        "/api/jobs",
        {
            "project_slug": project_slug,
            "job_type": "fix-bug",
            "title": "Smoke test dry run",
            "request_text": "Dry-run smoke test — does not spawn a driver.",
            "dry_run": True,
        },
    )
    assert status == 201, f"Expected 201, got {status}: {body}"
    assert body["dry_run"] is True, "Expected dry_run=true"
    assert isinstance(body["stages"], list) and len(body["stages"]) > 0, (
        "Expected non-empty stages list"
    )
    print(f"  job_slug (planned) : {body['job_slug']}")
    print(f"  stages compiled    : {len(body['stages'])}")
    for i, s in enumerate(body["stages"], 1):
        print(f"    {i:2}. {s.get('id', '?')}")
    print("  PASS")

    print("\n=== Test 2: compile error (unknown project) ===")
    status, body = post(
        "/api/jobs",
        {
            "project_slug": "no-such-project-xyzzy",
            "job_type": "fix-bug",
            "title": "Should fail",
            "request_text": "This should fail.",
        },
    )
    assert status == 422, f"Expected 422, got {status}: {body}"
    failures = body["detail"]
    assert isinstance(failures, list) and len(failures) > 0, "Expected failures list"
    assert failures[0]["kind"] == "project_not_found", f"Unexpected kind: {failures[0]['kind']}"
    print(f"  failure kind: {failures[0]['kind']}")
    print(f"  message     : {failures[0]['message']}")
    print("  PASS")

    print("\n=== Test 3: compile error (unknown job type) ===")
    status, body = post(
        "/api/jobs",
        {
            "project_slug": project_slug,
            "job_type": "nonexistent-job-type",
            "title": "Should fail",
            "request_text": "This should fail.",
        },
    )
    assert status == 422, f"Expected 422, got {status}: {body}"
    failures = body["detail"]
    assert any(f["kind"] == "template_not_found" for f in failures), (
        f"Expected template_not_found, got: {failures}"
    )
    print(f"  failure kind: {failures[0]['kind']}")
    print("  PASS")

    print("\n=== All Stage 14 smoke tests PASSED ===")
    print("\nNOTE: Real job submit (non-dry-run) spawns a driver process.")
    print(
        "      To exercise that path: submit via the dashboard UI at http://127.0.0.1:5173/jobs/new"
    )


if __name__ == "__main__":
    main()
