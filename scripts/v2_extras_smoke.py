"""HTTP-layer smoke test for hammock v2 extras.

Drives the dashboard's API surface (multi-artifact upload, workflows
CRUD, HIL submission) without spawning real claude. Uses HAMMOCK_V2_RUNNER_MODE=fake
to keep the runner from invoking the real binary.

Usage:
    .venv/bin/python scripts/v2_extras_smoke.py

Exits non-zero with a descriptive error on any failure.
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

# We construct the app via dashboard_v2.api.app:create_app and use a
# tmpdir HAMMOCK_V2_ROOT so we don't pollute the user's real data.


def _run() -> None:
    import os

    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / "jobs").mkdir()
    os.environ["HAMMOCK_V2_ROOT"] = str(root)
    os.environ["HAMMOCK_V2_RUNNER_MODE"] = "fake"

    from dashboard_v2.api.app import create_app

    app = create_app()
    with TestClient(app) as c:
        # 1. Workflows CRUD
        r = c.get("/api/workflows")
        assert r.status_code == 200, r.text
        data = r.json()
        names = [w.get("name") for w in data["workflows"]]
        assert "fix-bug" in names, f"bundled fix-bug missing: {names}"

        # Detail of bundled
        r = c.get("/api/workflows/fix-bug")
        assert r.status_code == 200, r.text
        detail = r.json()
        assert detail["bundled"] is True
        assert "yaml" in detail and detail["yaml"].strip()

        # Create a custom workflow
        custom_yaml = yaml.safe_dump(
            {
                "name": "smoke-test",
                "description": "smoke test workflow",
                "nodes": [
                    {"id": "first", "prompt": "write-bug-report"},
                    {
                        "id": "second",
                        "prompt": "write-design-spec",
                        "after": ["first"],
                    },
                ],
            },
            sort_keys=False,
        )
        r = c.post("/api/workflows", json={"name": "smoke-test", "yaml": custom_yaml})
        assert r.status_code == 201, r.text

        # Update
        custom_yaml2 = custom_yaml.replace("smoke test workflow", "smoke test workflow v2")
        r = c.put("/api/workflows/smoke-test", json={"yaml": custom_yaml2})
        assert r.status_code == 200, r.text

        # Bundled is read-only
        r = c.put("/api/workflows/fix-bug", json={"yaml": custom_yaml})
        assert r.status_code == 405, r.text

        # Delete user-defined
        r = c.delete("/api/workflows/smoke-test")
        assert r.status_code == 200, r.text

        # Validate endpoint returns nodes for live preview
        r = c.post("/api/workflows/validate", json={"yaml": custom_yaml})
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["valid"] is True
        assert len(v["nodes"]) == 2

        # Validate failure shape
        r = c.post("/api/workflows/validate", json={"yaml": "not: valid: yaml: at: all"})
        assert r.status_code == 200
        assert r.json()["valid"] is False

        # 2. Multi-artifact submission (fake runner)
        files = [
            ("artifacts", ("error.log", io.BytesIO(b"line1\nline2\n"), "text/plain")),
            ("artifacts", ("screenshot.png", io.BytesIO(b"\x89PNGfake"), "image/png")),
        ]
        r = c.post(
            "/api/jobs",
            data={"workflow": "fix-bug", "request": "smoke test"},
            files=files,
        )
        assert r.status_code in (200, 202), r.text
        slug = r.json()["slug"]

        # Inputs landed
        inputs_dir = root / "jobs" / slug / "inputs"
        assert inputs_dir.is_dir(), f"inputs dir missing: {inputs_dir}"
        names = sorted(p.name for p in inputs_dir.iterdir())
        assert "error.log" in names, names
        assert "screenshot.png" in names, names
        assert (inputs_dir / "error.log").read_bytes() == b"line1\nline2\n"

        # 3. HIL submission
        # Manually create a node with an awaiting_human marker, then POST a decision.
        node_dir = root / "jobs" / slug / "nodes" / "review-design-spec"
        node_dir.mkdir(parents=True, exist_ok=True)
        (node_dir / "state.md").write_text("---\nstate: running\n---\n")
        (node_dir / "awaiting_human.md").write_text("---\nawaiting_human_since: now\n---\n")
        (node_dir / "output.md").write_text("# Review\n\nLooks good.\n")

        r = c.post(
            f"/api/jobs/{slug}/nodes/review-design-spec/human_decision",
            json={"decision": "approved", "comment": "ok"},
        )
        assert r.status_code in (200, 201, 204), r.text
        assert (node_dir / "human_decision.md").is_file()
        decision_md = (node_dir / "human_decision.md").read_text()
        assert "approved" in decision_md, decision_md

        # Needs-revision flow
        node_dir2 = root / "jobs" / slug / "nodes" / "review-2"
        node_dir2.mkdir(parents=True, exist_ok=True)
        (node_dir2 / "state.md").write_text("---\nstate: running\n---\n")
        (node_dir2 / "awaiting_human.md").write_text("---\n---\n")
        (node_dir2 / "output.md").write_text("Body.\n")

        r = c.post(
            f"/api/jobs/{slug}/nodes/review-2/human_decision",
            json={"decision": "needs-revision", "comment": "more rigor please"},
        )
        assert r.status_code in (200, 201, 204), r.text
        body = (node_dir2 / "human_decision.md").read_text()
        assert "needs-revision" in body, body
        assert "more rigor please" in body, body

    print("v2 extras smoke OK")


if __name__ == "__main__":
    try:
        _run()
    except AssertionError as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"SMOKE ERROR: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
