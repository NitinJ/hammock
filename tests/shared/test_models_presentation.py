"""Tests for ``shared.models.presentation``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.models import PresentationBlock, UiTemplate
from tests.shared.factories import make_presentation_block, make_ui_template


def test_presentation_block_roundtrip() -> None:
    b = make_presentation_block()
    assert PresentationBlock.model_validate_json(b.model_dump_json()) == b


def test_presentation_block_requires_template() -> None:
    with pytest.raises(ValidationError):
        PresentationBlock.model_validate({"ui_template": ""})


def test_ui_template_roundtrip() -> None:
    t = make_ui_template()
    assert UiTemplate.model_validate_json(t.model_dump_json()) == t
