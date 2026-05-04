"""Per-schema HIL artifact payload builders.

Per docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step E and
spec D17: a plain dict keyed by artifact schema name. Adding a new
schema is one entry. The HIL gate stitcher dispatches to this
registry; missing schema → loud :class:`MissingBuilderError`.

Every builder's output must validate against the production
:mod:`shared.artifact_validators` schema for the same key — that's
the lock-down test in ``test_hil_builders.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class BuilderContext:
    """Inputs available to a builder when stitching a HIL gate."""

    job_dir: Path
    stage_id: str
    output_path: str
    schema: str


Builder = Callable[[BuilderContext], bytes]


class MissingBuilderError(KeyError):
    """No builder is registered for the requested schema."""

    def __init__(self, schema: str) -> None:
        super().__init__(schema)
        self.schema = schema

    def __str__(self) -> str:
        return (
            f"no fixture-builder registered for schema {self.schema!r}; "
            f"add one in tests/e2e/hil_builders.py"
        )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _build_non_empty(_ctx: BuilderContext) -> bytes:
    return b"placeholder content for hammock e2e test\n"


def _build_review_verdict(_ctx: BuilderContext) -> bytes:
    """Approve every review by default — keeps the lifecycle moving.

    Operator-controlled stitching can override this builder per-run if
    a "needs-revision" cycle is desired.
    """
    payload = {
        "verdict": "approved",
        "summary": "e2e test stitch: approved",
        "unresolved_concerns": [],
        "addressed_in_this_iteration": [],
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def _build_plan(_ctx: BuilderContext) -> bytes:
    """Empty plan — the expander stage produces no further stages.

    The Plan model accepts an empty stage list; this keeps the test
    deterministic. Templates that genuinely need expansion at the e2e
    layer can swap a richer builder per-run; that's a v1+ extension.
    """
    payload = {"stages": []}
    return yaml.safe_dump(payload, sort_keys=False).encode("utf-8")


def _build_integration_test_report(_ctx: BuilderContext) -> bytes:
    payload = {
        "verdict": "passed",
        "summary": "e2e test stitch: synthesised passing report",
        "test_command": "pytest",
        "total_count": 0,
        "passed_count": 0,
        "failed_count": 0,
        "skipped_count": 0,
        "failures": [],
        "duration_seconds": 0.0,
    }
    return json.dumps(payload, indent=2).encode("utf-8")


BUILDERS: dict[str, Builder] = {
    "non-empty": _build_non_empty,
    "review-verdict-schema": _build_review_verdict,
    "plan-schema": _build_plan,
    "integration-test-report-schema": _build_integration_test_report,
}


def build(schema: str, ctx: BuilderContext) -> bytes:
    """Dispatch to the registered builder for *schema*.

    Raises :class:`MissingBuilderError` if no builder is registered.
    """
    fn = BUILDERS.get(schema)
    if fn is None:
        raise MissingBuilderError(schema)
    return fn(ctx)
