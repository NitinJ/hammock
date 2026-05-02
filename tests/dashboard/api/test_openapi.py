"""Verify that ``/openapi.json`` is generated and consumable.

Per Stage 9 acceptance: ``/openapi.json`` must be consumable by
``openapi-typescript`` (frontend-side schema generation). We can't run
the npm tool here, but we verify the spec parses, references all our
routes, and resolves model schemas — the same checks
``openapi-typescript`` would gate on.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def test_openapi_json_returns_200(client: TestClient) -> None:
    with client:
        r = client.get("/openapi.json")
    assert r.status_code == 200


def test_openapi_routes_present(client: TestClient) -> None:
    with client:
        spec = client.get("/openapi.json").json()
    paths = spec["paths"]
    expected = [
        "/api/health",
        "/api/projects",
        "/api/projects/{slug}",
        "/api/jobs",
        "/api/jobs/{job_slug}",
        "/api/jobs/{job_slug}/stages/{stage_id}",
        "/api/active-stages",
        "/api/hil",
        "/api/hil/{item_id}",
        "/api/artifacts/{job_slug}/{file_path}",
        "/api/costs",
        "/api/observatory/metrics",
    ]
    missing = [p for p in expected if p not in paths]
    assert not missing, f"missing routes in openapi: {missing}"


def test_openapi_components_resolve(client: TestClient) -> None:
    """Every $ref must point to a defined component."""
    with client:
        spec = client.get("/openapi.json").json()
    schemas = set(spec.get("components", {}).get("schemas", {}).keys())

    def collect_refs(node: object, refs: set[str]) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "$ref" and isinstance(v, str):
                    # "#/components/schemas/<Name>"
                    refs.add(v.rsplit("/", 1)[-1])
                else:
                    collect_refs(v, refs)
        elif isinstance(node, list):
            for v in node:
                collect_refs(v, refs)

    refs: set[str] = set()
    collect_refs(spec["paths"], refs)
    missing = refs - schemas
    assert not missing, f"unresolved refs: {missing}"


def test_openapi_is_valid_json_string(client: TestClient) -> None:
    """Sanity: the served body is JSON, not a stray repr."""
    with client:
        r = client.get("/openapi.json")
    json.loads(r.text)
