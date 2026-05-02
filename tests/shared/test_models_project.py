"""Tests for ``shared.models.project``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from shared.models import Project, ProjectConfig
from tests.shared.factories import make_project


def test_factory_validates() -> None:
    p = make_project()
    assert p.slug == "figur-backend-v2"


def test_roundtrip_json() -> None:
    p = make_project()
    j = p.model_dump_json()
    assert ProjectConfig.model_validate_json(j) == p


def test_alias_project_eq_project_config() -> None:
    assert Project is ProjectConfig


def test_invalid_slug_rejected() -> None:
    with pytest.raises(ValidationError):
        Project(
            slug="Bad Slug",
            name="x",
            repo_path="/p",
            default_branch="main",
            created_at=make_project().created_at,
        )


def test_extra_field_forbidden() -> None:
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate({**make_project().model_dump(mode="json"), "extra_field": 1})
