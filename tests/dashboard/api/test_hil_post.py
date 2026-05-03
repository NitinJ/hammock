"""Tests for Stage-13 HIL write endpoints.

POST /api/hil/{id}/answer  — submit an answer
GET  /api/hil/{id}         — enriched HilItemDetail envelope
GET  /api/hil/templates/{name} — resolved template
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.settings import Settings
from shared import paths
from shared.atomic import atomic_write_json
from shared.models import AskQuestion, HilItem, JobConfig, JobState, ProjectConfig, ReviewQuestion
from shared.models.hil import ManualStepQuestion

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(offset: int = 0) -> datetime:
    return datetime(2026, 5, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=offset)


def _project(slug: str, repo_path: Path) -> ProjectConfig:
    return ProjectConfig(
        slug=slug,
        name=slug,
        repo_path=str(repo_path),
        remote_url=f"https://github.com/example/{slug}",
        default_branch="main",
        created_at=_ts(),
    )


def _job(*, slug: str, project: str) -> JobConfig:
    return JobConfig(
        job_id=f"id-{slug}",
        job_slug=slug,
        project_slug=project,
        job_type="fix-bug",
        created_at=_ts(),
        created_by="test",
        state=JobState.STAGES_RUNNING,
    )


def _hil_ask(item_id: str, *, stage_id: str = "s1") -> HilItem:
    return HilItem(
        id=item_id,
        kind="ask",
        stage_id=stage_id,
        created_at=_ts(),
        status="awaiting",
        question=AskQuestion(text="Use Argon2id?", options=["yes", "no"]),
    )


def _hil_review(item_id: str, *, stage_id: str = "s1") -> HilItem:
    return HilItem(
        id=item_id,
        kind="review",
        stage_id=stage_id,
        created_at=_ts(),
        status="awaiting",
        question=ReviewQuestion(target="design-spec.md", prompt="Approve spec?"),
    )


def _hil_manual(item_id: str, *, stage_id: str = "s1") -> HilItem:
    return HilItem(
        id=item_id,
        kind="manual-step",
        stage_id=stage_id,
        created_at=_ts(),
        status="awaiting",
        question=ManualStepQuestion(instructions="Deploy to staging.", extra_fields=None),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "hammock"
    r.mkdir()
    return r


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "my-project"
    d.mkdir()
    return d


@pytest.fixture
def seeded_root(root: Path, project_dir: Path) -> Path:
    proj = _project("alpha", project_dir)
    atomic_write_json(paths.project_json("alpha", root=root), proj)
    job = _job(slug="alpha-job-1", project="alpha")
    atomic_write_json(paths.job_json(job.job_slug, root=root), job)
    return root


@pytest.fixture
def client_with_items(seeded_root: Path) -> Iterator[TestClient]:
    ask = _hil_ask("hil-ask-1")
    review = _hil_review("hil-review-1")
    manual = _hil_manual("hil-manual-1")
    for item in (ask, review, manual):
        atomic_write_json(paths.hil_item_path("alpha-job-1", item.id, root=seeded_root), item)
    with TestClient(create_app(Settings(root=seeded_root))) as client:
        yield client


@pytest.fixture
def client_template_dir(seeded_root: Path) -> Iterator[tuple[TestClient, Path]]:
    tpl_dir = seeded_root / "ui-templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "ask-default-form.json").write_text(
        json.dumps(
            {
                "name": "ask-default-form",
                "hil_kinds": ["ask"],
                "instructions": "Answer the question.",
                "description": None,
                "fields": {"submit_label": "Submit Answer"},
            }
        )
    )
    with TestClient(create_app(Settings(root=seeded_root))) as client:
        yield client, tpl_dir


# ---------------------------------------------------------------------------
# GET /api/hil/{id} — HilItemDetail envelope
# ---------------------------------------------------------------------------


def test_get_hil_item_detail_ask(client_with_items: TestClient) -> None:
    """GET /api/hil/{id} returns HilItemDetail with job_slug, project_slug, template name."""
    resp = client_with_items.get("/api/hil/hil-ask-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["item"]["id"] == "hil-ask-1"
    assert body["item"]["kind"] == "ask"
    assert body["job_slug"] == "alpha-job-1"
    assert body["project_slug"] == "alpha"
    assert body["ui_template_name"] == "ask-default-form"


def test_get_hil_item_detail_review(client_with_items: TestClient) -> None:
    """Review items default to spec-review-form."""
    resp = client_with_items.get("/api/hil/hil-review-1")
    assert resp.status_code == 200
    assert resp.json()["ui_template_name"] == "spec-review-form"


def test_get_hil_item_detail_manual(client_with_items: TestClient) -> None:
    """Manual-step items default to manual-step-default-form."""
    resp = client_with_items.get("/api/hil/hil-manual-1")
    assert resp.status_code == 200
    assert resp.json()["ui_template_name"] == "manual-step-default-form"


def test_get_hil_item_detail_not_found(client_with_items: TestClient) -> None:
    """GET /api/hil/{id} returns 404 for unknown id."""
    assert client_with_items.get("/api/hil/does-not-exist").status_code == 404


# ---------------------------------------------------------------------------
# GET /api/hil/templates/{name}
# ---------------------------------------------------------------------------


def test_get_template_returns_resolved_template(
    client_template_dir: tuple[TestClient, Path],
) -> None:
    client, _ = client_template_dir
    resp = client.get("/api/hil/templates/ask-default-form")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "ask-default-form"
    assert body["hil_kinds"] == ["ask"]
    assert body["instructions"] == "Answer the question."


def test_get_template_not_found_returns_404(
    client_template_dir: tuple[TestClient, Path],
) -> None:
    client, _ = client_template_dir
    assert client.get("/api/hil/templates/no-such-template").status_code == 404


def test_get_template_uses_project_override(seeded_root: Path, project_dir: Path) -> None:
    """Per-project override is applied when project_slug query param is set."""
    tpl_dir = seeded_root / "ui-templates"
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "spec-review-form.json").write_text(
        json.dumps(
            {
                "name": "spec-review-form",
                "hil_kinds": ["review"],
                "instructions": "Global.",
                "description": None,
                "fields": None,
            }
        )
    )
    override_dir = project_dir / ".hammock" / "ui-templates"
    override_dir.mkdir(parents=True, exist_ok=True)
    (override_dir / "spec-review-form.json").write_text(
        json.dumps(
            {
                "name": "spec-review-form",
                "hil_kinds": None,
                "instructions": "Project override.",
                "description": None,
                "fields": None,
            }
        )
    )
    with TestClient(create_app(Settings(root=seeded_root))) as client:
        resp = client.get("/api/hil/templates/spec-review-form?project_slug=alpha")
    assert resp.status_code == 200
    assert resp.json()["instructions"] == "Project override."


# ---------------------------------------------------------------------------
# POST /api/hil/{id}/answer
# ---------------------------------------------------------------------------


def test_submit_ask_answer_transitions_to_answered(client_with_items: TestClient) -> None:
    """Submitting a valid ask answer transitions item to 'answered'."""
    payload = {"kind": "ask", "choice": "yes", "text": "Looks good."}
    resp = client_with_items.post("/api/hil/hil-ask-1/answer", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered"
    assert body["answer"]["choice"] == "yes"
    assert body["answer"]["text"] == "Looks good."


def test_submit_review_answer_transitions_to_answered(client_with_items: TestClient) -> None:
    """Submitting a valid review answer transitions item to 'answered'."""
    payload = {"kind": "review", "decision": "approve", "comments": "LGTM"}
    resp = client_with_items.post("/api/hil/hil-review-1/answer", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered"
    assert body["answer"]["decision"] == "approve"


def test_submit_manual_step_answer_transitions_to_answered(
    client_with_items: TestClient,
) -> None:
    """Submitting a valid manual-step answer transitions item to 'answered'."""
    payload = {"kind": "manual-step", "output": "Deployed successfully.", "extras": None}
    resp = client_with_items.post("/api/hil/hil-manual-1/answer", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "answered"
    assert body["answer"]["output"] == "Deployed successfully."


def test_submit_answer_not_found_returns_404(client_with_items: TestClient) -> None:
    """POST to unknown item_id returns 404."""
    payload = {"kind": "ask", "choice": None, "text": "whatever"}
    assert client_with_items.post("/api/hil/no-such-id/answer", json=payload).status_code == 404


def test_submit_answer_idempotent_same_answer(client_with_items: TestClient) -> None:
    """Re-submitting the identical answer is idempotent (200, no error)."""
    payload = {"kind": "ask", "choice": "yes", "text": "Agreed."}
    assert client_with_items.post("/api/hil/hil-ask-1/answer", json=payload).status_code == 200
    resp2 = client_with_items.post("/api/hil/hil-ask-1/answer", json=payload)
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "answered"


def test_submit_answer_conflict_different_answer_returns_409(
    client_with_items: TestClient,
) -> None:
    """Re-submitting a different answer to an already-answered item returns 409."""
    client_with_items.post(
        "/api/hil/hil-ask-1/answer", json={"kind": "ask", "choice": "yes", "text": "First."}
    )
    resp = client_with_items.post(
        "/api/hil/hil-ask-1/answer", json={"kind": "ask", "choice": "no", "text": "Changed."}
    )
    assert resp.status_code == 409


def test_submit_answer_wrong_kind_returns_422(client_with_items: TestClient) -> None:
    """Submitting review answer body to an ask item returns 422."""
    payload = {"kind": "review", "decision": "approve", "comments": "wrong kind"}
    assert client_with_items.post("/api/hil/hil-ask-1/answer", json=payload).status_code == 422


def test_submit_answer_persists_to_disk(seeded_root: Path) -> None:
    """Answered item is persisted to disk so a fresh cache reads it back."""
    ask = _hil_ask("persist-test")
    atomic_write_json(paths.hil_item_path("alpha-job-1", ask.id, root=seeded_root), ask)

    with TestClient(create_app(Settings(root=seeded_root))) as client:
        resp = client.post(
            "/api/hil/persist-test/answer",
            json={"kind": "ask", "choice": "yes", "text": "Yes."},
        )
    assert resp.status_code == 200

    on_disk = HilItem.model_validate_json(
        paths.hil_item_path("alpha-job-1", "persist-test", root=seeded_root).read_text()
    )
    assert on_disk.status == "answered"
    assert on_disk.answer is not None
