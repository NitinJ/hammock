"""Presentation block + UI template schemas.

Per design doc § Presentation plane § Form pipeline and template registry.

A stage definition's ``presentation`` block selects a ``ui_template`` by name;
the template is resolved per-project-first, fall-back-to-global, against:

- ``~/.hammock/ui-templates/<name>.json``                 (global default — kernel)
- ``<project_repo_root>/.hammock/ui-templates/<name>.json`` (per-project — tunable)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PresentationBlock(BaseModel):
    """The ``presentation:`` block on a stage definition."""

    model_config = ConfigDict(extra="forbid")

    ui_template: str = Field(min_length=1, description="template name")
    summary: str | None = Field(
        default=None,
        description="one-liner with template vars (e.g. ${job.title})",
    )


class UiTemplate(BaseModel):
    """A UI template loaded from disk.

    Stored as JSON for trivial diff-ability, override-merging, and Soul
    inspection (vs. Vue SFCs which would require runtime compilation).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str | None = None
    hil_kinds: list[str] | None = None
    fields: dict[str, Any] | None = None
    instructions: str | None = None
