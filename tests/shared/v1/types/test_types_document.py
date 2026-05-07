"""Stage 2 — narrative artifact types carry a ``document: str`` field.

Per ``docs/hammock-workflow.md`` §"The ``document`` field": every type
whose primary content is prose carries a ``document: str`` of markdown
alongside its structured fields. The dashboard renders ``document`` as
the primary view.

Types that opt in here:

- bug-report
- design-spec
- impl-spec
- impl-plan
- summary

Types intentionally **not** changed (still tested elsewhere — should
continue to validate without ``document``):

- review-verdict, pr-review-verdict (short-form, has ``summary`` already)
- pr (no narrative)
- job-request (raw user input)
- list[T] (wrapper)

Each parametrized test asserts the same shape for every opt-in type:

- The Pydantic model rejects payloads missing ``document``.
- The Pydantic model rejects payloads with empty ``document``.
- A payload that *does* include ``document`` round-trips through
  ``produce()`` and ``render_for_consumer()``.
- The ``render_for_producer()`` schema hint mentions ``document``.
- The ``render_for_consumer()`` output inlines the document body.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from shared.v1.types.protocol import VariableTypeError
from shared.v1.types.registry import get_type


@dataclass
class FakeNodeCtx:
    var_name: str
    job_dir: Path

    def expected_path(self) -> Path:
        return self.job_dir / f"{self.var_name}.json"


@dataclass
class FakePromptCtx:
    var_name: str
    job_dir: Path

    def expected_path(self) -> Path:
        return self.job_dir / "variables" / f"{self.var_name}.json"


# ---------------------------------------------------------------------------
# Per-type minimum payload (everything the type requires *besides* document).
# Tests overlay ``document`` on top of these to verify the field-level
# behaviour without coupling the test to each type's other required fields.
# ---------------------------------------------------------------------------


_BASE_PAYLOADS: dict[str, dict] = {
    "bug-report": {"summary": "the bug"},
    "design-spec": {"title": "t", "overview": "ov"},
    "impl-spec": {"title": "t", "overview": "ov"},
    "impl-plan": {"count": 1, "stages": []},
    "summary": {"text": "x"},
}

_NARRATIVE_TYPES = sorted(_BASE_PAYLOADS.keys())


@pytest.mark.parametrize("type_name", _NARRATIVE_TYPES)
def test_narrative_type_rejects_payload_without_document(type_name: str, tmp_path: Path) -> None:
    """Payload missing ``document`` is rejected by ``produce()``."""
    payload = _BASE_PAYLOADS[type_name].copy()
    var_name = type_name.replace("-", "_")
    (tmp_path / f"{var_name}.json").write_text(json.dumps(payload))
    t = get_type(type_name)
    with pytest.raises(VariableTypeError, match="document"):
        t.produce(t.Decl(), FakeNodeCtx(var_name=var_name, job_dir=tmp_path))


@pytest.mark.parametrize("type_name", _NARRATIVE_TYPES)
def test_narrative_type_rejects_empty_document(type_name: str, tmp_path: Path) -> None:
    """An empty ``document`` is rejected (min_length=1)."""
    payload = _BASE_PAYLOADS[type_name].copy()
    payload["document"] = ""
    var_name = type_name.replace("-", "_")
    (tmp_path / f"{var_name}.json").write_text(json.dumps(payload))
    t = get_type(type_name)
    with pytest.raises(VariableTypeError, match=r"schema invalid|document"):
        t.produce(t.Decl(), FakeNodeCtx(var_name=var_name, job_dir=tmp_path))


@pytest.mark.parametrize("type_name", _NARRATIVE_TYPES)
def test_narrative_type_round_trips_with_document(type_name: str, tmp_path: Path) -> None:
    """Payload with ``document`` round-trips: produce() returns a value
    whose ``document`` attribute matches what was written."""
    payload = _BASE_PAYLOADS[type_name].copy()
    payload["document"] = "## Heading\n\nMarkdown body with **bold**.\n"
    var_name = type_name.replace("-", "_")
    (tmp_path / f"{var_name}.json").write_text(json.dumps(payload))
    t = get_type(type_name)
    value = t.produce(t.Decl(), FakeNodeCtx(var_name=var_name, job_dir=tmp_path))
    assert getattr(value, "document") == "## Heading\n\nMarkdown body with **bold**.\n"


@pytest.mark.parametrize("type_name", _NARRATIVE_TYPES)
def test_render_for_producer_mentions_document(type_name: str, tmp_path: Path) -> None:
    """The footer instruction (render_for_producer) tells the agent to
    fill the ``document`` field with markdown."""
    t = get_type(type_name)
    rendered = t.render_for_producer(t.Decl(), FakePromptCtx(var_name="x", job_dir=tmp_path))
    # Engine controls the contract — every narrative type's footer must
    # call out the document field by name and as markdown.
    assert "document" in rendered, f"{type_name}: render_for_producer does not mention 'document'"
    assert "markdown" in rendered.lower(), (
        f"{type_name}: render_for_producer does not mention 'markdown'"
    )


@pytest.mark.parametrize("type_name", _NARRATIVE_TYPES)
def test_render_for_consumer_inlines_document(type_name: str, tmp_path: Path) -> None:
    """The header inlines each input — for narrative types, the
    consumer rendering must include the markdown ``document`` body so
    the agent reads it without a tool round-trip."""
    payload = _BASE_PAYLOADS[type_name].copy()
    sentinel = "INLINED-DOCUMENT-SENTINEL-9X9X9X"
    payload["document"] = f"## Body\n\n{sentinel}\n"
    t = get_type(type_name)
    value = t.Value.model_validate(payload)
    rendered = t.render_for_consumer(t.Decl(), value, FakePromptCtx(var_name="v", job_dir=tmp_path))
    assert sentinel in rendered, (
        f"{type_name}: render_for_consumer did not inline the document body"
    )


# ---------------------------------------------------------------------------
# Smoke test: types intentionally *without* document still validate
# ---------------------------------------------------------------------------


def test_pr_type_does_not_require_document(tmp_path: Path) -> None:
    """`pr` is non-narrative (branch + commit metadata); adding document
    here would be wrong. This guards against accidentally promoting
    document to a universal contract."""
    from shared.v1.types.pr import PRValue

    # Smoke check: PRValue still has no ``document`` field.
    assert "document" not in PRValue.model_fields, (
        "pr type unexpectedly carries a `document` field — Stage 2 only "
        "adds document to narrative types"
    )


def test_review_verdict_does_not_require_document(tmp_path: Path) -> None:
    """`review-verdict` keeps its short-form ``summary`` field; document
    is not added here in v1."""
    from shared.v1.types.review_verdict import ReviewVerdictValue

    val = ReviewVerdictValue(verdict="approved", summary="ok")
    assert getattr(val, "document", None) is None
