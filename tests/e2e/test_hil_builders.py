"""Tests for ``tests.e2e.hil_builders``.

The fixture-builder registry keys per-artifact-schema "what would a
sane operator write here" payloads. Each registered builder must
produce bytes that the production ``shared.artifact_validators``
registry validates as schema-conformant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.artifact_validators import REGISTRY as PRODUCTION_VALIDATORS
from tests.e2e.hil_builders import (
    BUILDERS,
    BuilderContext,
    MissingBuilderError,
    build,
)


def _ctx(tmp_path: Path, schema: str) -> BuilderContext:
    return BuilderContext(
        job_dir=tmp_path,
        stage_id="some-stage",
        output_path="out.txt",
        schema=schema,
    )


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_missing_builder_raises_named_error(tmp_path: Path) -> None:
    with pytest.raises(MissingBuilderError, match="unknown-schema"):
        build("unknown-schema", _ctx(tmp_path, "unknown-schema"))


def test_registry_covers_every_schema_in_bundled_templates() -> None:
    """Every schema referenced by the bundled job templates must have a
    registered builder. Detected by parsing the YAML and intersecting
    with our BUILDERS dict."""
    import re

    schemas: set[str] = set()
    for tpl in Path("hammock/templates/job-templates").glob("*.yaml"):
        for match in re.finditer(r"schema:\s*([a-z0-9-]+)", tpl.read_text()):
            schemas.add(match.group(1))
    schemas.add("non-empty")  # generic; not declared in templates but used
    missing = schemas - BUILDERS.keys()
    assert not missing, f"schemas without a builder: {missing}"


# ---------------------------------------------------------------------------
# Each builder produces a payload its production validator accepts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema", sorted(BUILDERS))
def test_each_builder_validates_against_production_schema(tmp_path: Path, schema: str) -> None:
    """Lock-down: build payload → write to disk → run prod validator
    → must return None (no error). This is the reason the registry
    earns its keep."""
    payload = build(schema, _ctx(tmp_path, schema))
    out = tmp_path / "out.txt"
    out.write_bytes(payload)
    validator = PRODUCTION_VALIDATORS[schema]
    err = validator(out)
    assert err is None, f"{schema!r} builder produced invalid payload: {err}"


@pytest.mark.parametrize("schema", sorted(BUILDERS))
def test_each_builder_returns_bytes(tmp_path: Path, schema: str) -> None:
    payload = build(schema, _ctx(tmp_path, schema))
    assert isinstance(payload, bytes)
    assert payload  # non-empty


# ---------------------------------------------------------------------------
# Surface
# ---------------------------------------------------------------------------


def test_builder_context_is_frozen(tmp_path: Path) -> None:
    """Context is a value type; helpers shouldn't mutate it."""
    ctx = _ctx(tmp_path, "non-empty")
    with pytest.raises(Exception):
        ctx.schema = "review-verdict-schema"  # type: ignore[misc]
