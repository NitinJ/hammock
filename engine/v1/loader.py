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
            f"workflow YAML at {path} must be a top-level mapping, got "
            f"{type(data).__name__}"
        )
    try:
        return Workflow.model_validate(data)
    except ValidationError as exc:
        raise WorkflowLoadError(
            f"workflow schema validation failed for {path}:\n{exc}"
        ) from exc
