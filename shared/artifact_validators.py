"""Named artifact validator registry.

Each validator is a callable ``(path: Path) -> str | None``; returns an error
message on failure, ``None`` on success.

Registry is keyed by the string name declared in plan YAML::

    exit_condition:
      required_outputs:
        - path: review.json
          validators: [review-verdict-schema]
      artifact_validators:
        - path: review.json
          schema: review-verdict-schema

Adding a new validator: define a ``_fn(path: Path) -> str | None`` function
below and add it to ``REGISTRY``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

ValidatorFn = Callable[[Path], str | None]


def _non_empty(path: Path) -> str | None:
    """File must exist and contain non-trivial content."""
    try:
        raw = path.read_bytes()
    except OSError as e:
        return f"cannot read file: {e}"
    if not raw.strip():
        return "file is empty"
    if path.suffix == ".json":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None  # Syntax errors are not our job to report here.
        if parsed in ({}, [], None, ""):
            return "file contains trivially empty JSON value"
    return None


def _review_verdict_schema(path: Path) -> str | None:
    """File must be valid JSON matching the canonical ReviewVerdict schema."""
    from shared.models.verdict import ReviewVerdict

    try:
        data = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as e:
        return f"cannot parse JSON: {e}"
    try:
        ReviewVerdict.model_validate(data)
    except Exception as e:
        return str(e)
    return None


def _plan_schema(path: Path) -> str | None:
    """File must be valid YAML matching the Plan (ordered stage list) schema."""
    import yaml

    from shared.models.plan import Plan

    try:
        data = yaml.safe_load(path.read_bytes())
    except (OSError, Exception) as e:
        return f"cannot parse YAML: {e}"
    try:
        Plan.model_validate(data)
    except Exception as e:
        return str(e)
    return None


def _integration_test_report_schema(path: Path) -> str | None:
    """File must be valid JSON matching the IntegrationTestReport schema."""
    from shared.models.integration_test_report import IntegrationTestReport

    try:
        data = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as e:
        return f"cannot parse JSON: {e}"
    try:
        IntegrationTestReport.model_validate(data)
    except Exception as e:
        return str(e)
    return None


REGISTRY: dict[str, ValidatorFn] = {
    "non-empty": _non_empty,
    "review-verdict-schema": _review_verdict_schema,
    "plan-schema": _plan_schema,
    "integration-test-report-schema": _integration_test_report_schema,
}
