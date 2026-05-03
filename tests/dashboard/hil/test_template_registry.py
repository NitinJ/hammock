"""Tests for dashboard.hil.template_registry.

TDD red phase — all tests fail until the implementation is written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.hil.template_registry import (
    TemplateKindConflictError,
    TemplateNotFoundError,
    TemplateRegistry,
)


def _write_template(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _global_path(root: Path, name: str) -> Path:
    return root / "ui-templates" / f"{name}.json"


def _project_path(repo: Path, name: str) -> Path:
    return repo / ".hammock" / "ui-templates" / f"{name}.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "hammock"
    r.mkdir()
    return r


@pytest.fixture
def registry(root: Path) -> TemplateRegistry:
    return TemplateRegistry(root=root)


@pytest.fixture
def project_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "my-project"
    repo.mkdir()
    return repo


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


def test_resolve_global_template(root: Path, registry: TemplateRegistry) -> None:
    """Resolves a template from the global ui-templates directory."""
    _write_template(
        _global_path(root, "ask-default-form"),
        {
            "name": "ask-default-form",
            "hil_kinds": ["ask"],
            "instructions": "Answer the question below.",
            "description": None,
            "fields": None,
        },
    )

    template = registry.resolve("ask-default-form")

    assert template.name == "ask-default-form"
    assert template.hil_kinds == ["ask"]
    assert template.instructions == "Answer the question below."


def test_resolve_uses_project_override_when_present(
    root: Path, registry: TemplateRegistry, project_repo: Path
) -> None:
    """Per-project override takes precedence over global template."""
    _write_template(
        _global_path(root, "spec-review-form"),
        {
            "name": "spec-review-form",
            "hil_kinds": ["review"],
            "instructions": "Global instructions.",
            "description": "global",
            "fields": {"submit_label": "Submit"},
        },
    )
    _write_template(
        _project_path(project_repo, "spec-review-form"),
        {
            "name": "spec-review-form",
            "hil_kinds": ["review"],
            "instructions": "Project-specific instructions.",
            "description": "override",
            "fields": {"submit_label": "Record Decision"},
        },
    )

    template = registry.resolve("spec-review-form", project_repo=project_repo)

    assert template.instructions == "Project-specific instructions."
    assert template.description == "override"
    assert template.fields is not None
    assert template.fields["submit_label"] == "Record Decision"


def test_resolve_falls_back_to_global_when_no_project_override(
    root: Path, registry: TemplateRegistry, project_repo: Path
) -> None:
    """Falls back to global when project has no override."""
    _write_template(
        _global_path(root, "manual-step-default-form"),
        {
            "name": "manual-step-default-form",
            "hil_kinds": ["manual-step"],
            "instructions": "Perform the step described below.",
            "description": None,
            "fields": None,
        },
    )
    # No project-level override written

    template = registry.resolve("manual-step-default-form", project_repo=project_repo)

    assert template.name == "manual-step-default-form"
    assert template.instructions == "Perform the step described below."


def test_resolve_neither_raises_not_found(registry: TemplateRegistry) -> None:
    """Raises TemplateNotFoundError when neither global nor project file exists."""
    with pytest.raises(TemplateNotFoundError, match="ask-default-form"):
        registry.resolve("ask-default-form")


def test_resolve_not_found_with_project_repo(
    registry: TemplateRegistry, project_repo: Path
) -> None:
    """Raises TemplateNotFoundError even when project_repo is given but both missing."""
    with pytest.raises(TemplateNotFoundError, match="unknown-template"):
        registry.resolve("unknown-template", project_repo=project_repo)


# ---------------------------------------------------------------------------
# Override validation — cannot change hil_kinds
# ---------------------------------------------------------------------------


def test_override_cannot_change_hil_kinds(
    root: Path, registry: TemplateRegistry, project_repo: Path
) -> None:
    """Raises TemplateKindConflictError when override has different hil_kinds."""
    _write_template(
        _global_path(root, "spec-review-form"),
        {
            "name": "spec-review-form",
            "hil_kinds": ["review"],
            "instructions": "Global instructions.",
            "description": None,
            "fields": None,
        },
    )
    _write_template(
        _project_path(project_repo, "spec-review-form"),
        {
            "name": "spec-review-form",
            "hil_kinds": ["ask"],  # wrong — changing kind is forbidden
            "instructions": "Override instructions.",
            "description": None,
            "fields": None,
        },
    )

    with pytest.raises(TemplateKindConflictError, match="hil_kinds"):
        registry.resolve("spec-review-form", project_repo=project_repo)


def test_override_with_null_hil_kinds_uses_base(
    root: Path, registry: TemplateRegistry, project_repo: Path
) -> None:
    """Override with hil_kinds=None keeps base hil_kinds (not a conflict)."""
    _write_template(
        _global_path(root, "spec-review-form"),
        {
            "name": "spec-review-form",
            "hil_kinds": ["review"],
            "instructions": "Global.",
            "description": None,
            "fields": None,
        },
    )
    _write_template(
        _project_path(project_repo, "spec-review-form"),
        {
            "name": "spec-review-form",
            "hil_kinds": None,  # null = no change (allowed)
            "instructions": "Override.",
            "description": None,
            "fields": None,
        },
    )

    template = registry.resolve("spec-review-form", project_repo=project_repo)

    assert template.hil_kinds == ["review"]  # preserved from base
    assert template.instructions == "Override."


def test_override_merges_fields(root: Path, registry: TemplateRegistry, project_repo: Path) -> None:
    """Override fields dict replaces (not merges) the base fields dict."""
    _write_template(
        _global_path(root, "spec-review-form"),
        {
            "name": "spec-review-form",
            "hil_kinds": ["review"],
            "instructions": None,
            "description": None,
            "fields": {"submit_label": "Submit", "extra_help": "Base help"},
        },
    )
    _write_template(
        _project_path(project_repo, "spec-review-form"),
        {
            "name": "spec-review-form",
            "hil_kinds": None,
            "instructions": None,
            "description": None,
            "fields": {"submit_label": "Override submit"},
        },
    )

    template = registry.resolve("spec-review-form", project_repo=project_repo)

    assert template.fields is not None
    assert template.fields["submit_label"] == "Override submit"


# ---------------------------------------------------------------------------
# Bundled templates fallback
# ---------------------------------------------------------------------------


def test_resolve_falls_back_to_bundled_dir(tmp_path: Path) -> None:
    """When global dir is empty, bundled_dir is used as fallback."""
    root = tmp_path / "hammock"
    root.mkdir()
    bundled = tmp_path / "bundled"
    _write_template(
        _global_path(bundled, "ask-default-form"),
        {
            "name": "ask-default-form",
            "hil_kinds": ["ask"],
            "instructions": "Bundled instructions.",
            "description": None,
            "fields": None,
        },
    )

    registry = TemplateRegistry(root=root, bundled_dir=bundled / "ui-templates")
    template = registry.resolve("ask-default-form")

    assert template.instructions == "Bundled instructions."


def test_resolve_global_takes_precedence_over_bundled(tmp_path: Path) -> None:
    """Global dir wins over bundled dir when both have the template."""
    root = tmp_path / "hammock"
    root.mkdir()
    bundled = tmp_path / "bundled"
    _write_template(
        _global_path(root, "ask-default-form"),
        {
            "name": "ask-default-form",
            "hil_kinds": ["ask"],
            "instructions": "Global instructions.",
            "description": None,
            "fields": None,
        },
    )
    _write_template(
        _global_path(bundled, "ask-default-form"),
        {
            "name": "ask-default-form",
            "hil_kinds": ["ask"],
            "instructions": "Bundled instructions.",
            "description": None,
            "fields": None,
        },
    )

    registry = TemplateRegistry(root=root, bundled_dir=bundled / "ui-templates")
    template = registry.resolve("ask-default-form")

    assert template.instructions == "Global instructions."
