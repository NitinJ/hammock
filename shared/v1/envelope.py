"""Variable envelope — engine-owned wrapper for every persisted typed value.

Per design-patch §1.7 and codex review #4: every persisted variable is wrapped
in an envelope carrying type identity, schema version, repo identity (for
substrate-aware values like `pr` and `branch`), producer-node id, and the
typed value payload. Resume after crash, or re-mounting the job dir in a
different repo context, is safe because the envelope is self-describing.

Type implementations don't see the envelope — they only own the `value`
payload. The engine wraps/unwraps.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Envelope(BaseModel):
    """Top-level wrapper persisted alongside every typed variable on disk."""

    model_config = ConfigDict(extra="forbid")

    type: str
    """The variable type's `name` (matches an entry in the type registry)."""

    type_version: int = 1
    """Bumped when a type's `Value` schema makes a breaking change."""

    repo: str | None = None
    """``owner/repo`` slug for substrate-aware types (`pr`, `branch`).
    None for types that have no repo identity."""

    producer_node: str
    """The node id that produced this variable. Helpful for tracing and
    for the validator's single-producer rule."""

    produced_at: datetime
    """UTC timestamp at which `produce` was called for this variable."""

    value: dict[str, Any] | list[Any]
    """The typed value, dumped from the type's `Value` Pydantic model.
    Stored as a JSON-serialisable structure (dict for scalar types, list
    for ``list[T]`` aggregates produced by count loops)."""


def make_envelope(
    *,
    type_name: str,
    producer_node: str,
    value_payload: dict[str, Any] | list[Any],
    repo: str | None = None,
    type_version: int = 1,
    now: datetime | None = None,
) -> Envelope:
    """Construct an Envelope. Defaults `produced_at` to now-in-UTC."""
    return Envelope(
        type=type_name,
        type_version=type_version,
        repo=repo,
        producer_node=producer_node,
        produced_at=now or datetime.now(UTC),
        value=value_payload,
    )


class EnvelopeMismatch(Exception):
    """Raised when a deserialised envelope's metadata does not match what
    the caller expected (different type, incompatible version, etc.)."""


def expect(envelope: Envelope, *, type_name: str, type_version: int = 1) -> None:
    """Sanity-check an envelope's metadata against an expected type +
    version. Raises ``EnvelopeMismatch`` on disagreement.

    Variable types call this in their ``deserialize`` before unpacking the
    payload — catching "I just deserialised a `branch` envelope as if it
    were a `pr`" before the wrong fields cause confusion."""
    if envelope.type != type_name:
        raise EnvelopeMismatch(
            f"envelope type mismatch: expected {type_name!r}, found {envelope.type!r}"
        )
    if envelope.type_version != type_version:
        raise EnvelopeMismatch(
            f"envelope type_version mismatch for {type_name!r}: "
            f"expected v{type_version}, found v{envelope.type_version}"
        )


def envelope_filename(variable_name: str) -> str:
    """Conventional filename for a persisted variable on disk.

    Engine owns the layout (design-patch §1.7); types don't see paths.
    Flat layout per loop scope, e.g. ``<job_dir>/variables/bug_report.json``
    or ``<job_dir>/loop_implement-loop_pr_5.json`` for indexed loop output."""
    return f"{variable_name}.json"
