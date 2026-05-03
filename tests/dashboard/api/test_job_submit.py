"""Tests for POST /api/jobs — job submit + Plan Compiler integration.

Stage 14. TDD: these tests are written BEFORE the endpoint implementation.

Covers:
- Happy path (real compile, mocked spawn_driver) → 201 + job_slug
- Dry-run → 201 + stages list, no job dir written, driver not spawned
- Unknown project → 422 with structured compile failures
- Unknown job_type → 422 with structured compile failures
- spawn_driver called with correct slug and root on real submit
- spawn_driver NOT called on dry_run
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from shared import paths

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUBMIT_URL = "/api/jobs"

_VALID_BODY: dict = {
    "project_slug": "alpha",
    "job_type": "fix-bug",
    "title": "Fix login crash",
    "request_text": "The login form crashes when the password field is left empty.",
}


def _post(client: TestClient, body: dict, *, mock_spawn: bool = True):
    if mock_spawn:
        with patch(
            "dashboard.api.jobs.spawn_driver",
            new_callable=AsyncMock,
            return_value=12345,
        ) as mock:
            resp = client.post(_SUBMIT_URL, json=body)
        return resp, mock
    return client.post(_SUBMIT_URL, json=body), None


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_submit_returns_201_with_job_slug(client: TestClient) -> None:
    resp, _ = _post(client, _VALID_BODY)
    assert resp.status_code == 201
    body = resp.json()
    assert "job_slug" in body
    assert isinstance(body["job_slug"], str)
    assert len(body["job_slug"]) > 0


def test_submit_response_dry_run_false(client: TestClient) -> None:
    resp, _ = _post(client, _VALID_BODY)
    assert resp.json()["dry_run"] is False


def test_submit_response_stages_null_for_real_submit(client: TestClient) -> None:
    resp, _ = _post(client, _VALID_BODY)
    assert resp.json()["stages"] is None


def test_submit_writes_job_dir(client: TestClient, populated_root: Path) -> None:
    resp, _ = _post(client, _VALID_BODY)
    job_slug = resp.json()["job_slug"]
    assert paths.job_dir(job_slug, root=populated_root).exists()


def test_submit_spawns_driver_with_job_slug(client: TestClient) -> None:
    resp, mock = _post(client, _VALID_BODY)
    job_slug = resp.json()["job_slug"]
    mock.assert_awaited_once()
    # First positional arg must be the job_slug
    assert mock.call_args.args[0] == job_slug


def test_submit_spawns_driver_with_correct_root(client: TestClient, populated_root: Path) -> None:
    _, mock = _post(client, _VALID_BODY)
    assert mock.call_args.kwargs.get("root") == populated_root


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_returns_201(client: TestClient) -> None:
    resp = client.post(_SUBMIT_URL, json={**_VALID_BODY, "dry_run": True})
    assert resp.status_code == 201


def test_dry_run_response_dry_run_true(client: TestClient) -> None:
    resp = client.post(_SUBMIT_URL, json={**_VALID_BODY, "dry_run": True})
    assert resp.json()["dry_run"] is True


def test_dry_run_returns_stages_list(client: TestClient) -> None:
    resp = client.post(_SUBMIT_URL, json={**_VALID_BODY, "dry_run": True})
    stages = resp.json()["stages"]
    assert isinstance(stages, list)
    assert len(stages) > 0
    assert all("id" in s for s in stages)


def test_dry_run_does_not_write_job_dir(client: TestClient, populated_root: Path) -> None:
    resp = client.post(_SUBMIT_URL, json={**_VALID_BODY, "dry_run": True})
    job_slug = resp.json()["job_slug"]
    assert not paths.job_dir(job_slug, root=populated_root).exists()


def test_dry_run_does_not_spawn_driver(client: TestClient) -> None:
    with patch(
        "dashboard.api.jobs.spawn_driver",
        new_callable=AsyncMock,
    ) as mock:
        client.post(_SUBMIT_URL, json={**_VALID_BODY, "dry_run": True})
    mock.assert_not_called()


# ---------------------------------------------------------------------------
# Compile failures → 422
# ---------------------------------------------------------------------------


def test_unknown_project_returns_422(client: TestClient) -> None:
    resp = client.post(
        _SUBMIT_URL,
        json={**_VALID_BODY, "project_slug": "no-such-project"},
    )
    assert resp.status_code == 422


def test_unknown_project_failure_kind(client: TestClient) -> None:
    resp = client.post(
        _SUBMIT_URL,
        json={**_VALID_BODY, "project_slug": "no-such-project"},
    )
    failures = resp.json()["detail"]
    assert isinstance(failures, list) and len(failures) > 0
    assert failures[0]["kind"] == "project_not_found"


def test_unknown_job_type_returns_422(client: TestClient) -> None:
    resp = client.post(
        _SUBMIT_URL,
        json={**_VALID_BODY, "job_type": "non-existent-type"},
    )
    assert resp.status_code == 422


def test_unknown_job_type_failure_kind(client: TestClient) -> None:
    resp = client.post(
        _SUBMIT_URL,
        json={**_VALID_BODY, "job_type": "non-existent-type"},
    )
    failures = resp.json()["detail"]
    assert any(f["kind"] == "template_not_found" for f in failures)


def test_failure_detail_has_required_fields(client: TestClient) -> None:
    resp = client.post(
        _SUBMIT_URL,
        json={**_VALID_BODY, "project_slug": "no-such-project"},
    )
    failure = resp.json()["detail"][0]
    assert "kind" in failure
    assert "stage_id" in failure
    assert "message" in failure


# ---------------------------------------------------------------------------
# Request body validation (FastAPI 422, not compile 422)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_slug", ""),
        ("job_type", ""),
        ("title", ""),
        ("request_text", ""),
    ],
)
def test_empty_required_field_returns_422(client: TestClient, field: str, value: str) -> None:
    body = {**_VALID_BODY, field: value}
    resp = client.post(_SUBMIT_URL, json=body)
    assert resp.status_code == 422


def test_extra_field_returns_422(client: TestClient) -> None:
    body = {**_VALID_BODY, "unexpected_field": "oops"}
    resp = client.post(_SUBMIT_URL, json=body)
    assert resp.status_code == 422
