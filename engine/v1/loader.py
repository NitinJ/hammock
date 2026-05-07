"""YAML loader for Hammock v1 workflows.

Per design-patch §1.2. Reads a YAML file and returns a validated `Workflow`
model. Pydantic does the structural validation; the workflow validator
(`engine/v1/validator.py`) does the cross-field semantic checks.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from shared.v1.workflow import Workflow


class WorkflowLoadError(Exception):
    """Raised when a workflow YAML cannot be loaded — missing file,
    malformed YAML, or schema validation failure."""


def load_workflow(path: Path) -> Workflow:
    """Load and parse a workflow YAML.

    Raises ``WorkflowLoadError`` with a context-rich message; never lets a
    bare ``yaml.YAMLError`` or ``ValidationError`` escape.
    """
    if not path.is_file():
        raise WorkflowLoadError(f"workflow YAML not found at {path}")
    try:
        text = path.read_text()
    except OSError as exc:
        raise WorkflowLoadError(f"could not read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WorkflowLoadError(f"YAML parse error in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkflowLoadError(
            f"workflow YAML at {path} must be a top-level mapping, got {type(data).__name__}"
        )

    # Stage 4 — friendlier error for the schema_version chokepoint. The
    # generic Pydantic message ("Field required" or "Input should be 1")
    # doesn't tell the operator whether to upgrade hammock or roll back
    # the workflow; this wrapping does.
    if "schema_version" not in data:
        raise WorkflowLoadError(
            f"workflow at {path} is missing the required `schema_version` field. "
            "Add `schema_version: 1` to the top of the yaml. This field has been "
            "mandatory since Stage 4."
        )
    if data["schema_version"] != 1:
        raise WorkflowLoadError(
            f"workflow at {path} has schema_version: {data['schema_version']!r}; "
            "this hammock supports up to 1. Upgrade hammock or roll back the "
            "workflow to schema_version: 1."
        )

    try:
        return Workflow.model_validate(data)
    except ValidationError as exc:
        raise WorkflowLoadError(f"workflow schema validation failed for {path}:\n{exc}") from exc
