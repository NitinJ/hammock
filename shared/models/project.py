"""Project registry schemas.

Per design doc § Project Registry. Identity is a path-derived immutable slug
distinct from the mutable display name. Registration creates the directory
``~/.hammock/projects/<slug>/`` and an empty override skeleton at
``<repo_path>/.hammock/`` (gitignored).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.slug import validate_slug


class ProjectConfig(BaseModel):
    """Persisted to ``~/.hammock/projects/<slug>/project.json``.

    The slug is immutable post-registration; the display name and metadata
    are mutable.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(description="path-derived kebab-case identifier; immutable")
    name: str = Field(min_length=1, description="mutable display name")
    repo_path: str = Field(min_length=1, description="absolute path to project repo")
    remote_url: str | None = Field(default=None, description="GitHub remote URL")
    default_branch: str = Field(min_length=1, description="default branch name")
    created_at: datetime

    # Health — set by ``hammock project doctor``; advisory only. Per design doc
    # § Project Registry § `project.json` schema. Both default to None on a
    # never-doctor'd project.
    last_health_check_at: datetime | None = None
    last_health_check_status: Literal["pass", "warn", "fail"] | None = None

    @field_validator("slug")
    @classmethod
    def _slug_must_be_canonical(cls, v: str) -> str:
        validate_slug(v)
        return v


# v0 alias — design doc and impl plan refer to ``Project`` and ``ProjectConfig``
# interchangeably; v0 keeps a single shape.
Project = ProjectConfig
