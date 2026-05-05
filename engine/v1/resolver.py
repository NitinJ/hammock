"""Variable resolver — translates `$var` and `$var.field` references in
node inputs into concrete values from the engine's variable store.

T1 scope: scalar variables only (no loops, no `$loop-id.var[i]`). Handles
required and optional inputs, and field-access on Pydantic-model values.

Future stages extend ``resolve_input`` to handle loop indexing per
design-patch §1.5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from shared.v1 import paths
from shared.v1.envelope import Envelope
from shared.v1.types.registry import get_type
from shared.v1.workflow import ArtifactNode, Workflow

_VAR_REF_RE = re.compile(r"^\$([a-zA-Z][a-zA-Z0-9_-]*)((?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)$")
_LOOP_VAR_REF_RE = re.compile(
    r"^\$(?P<loop_id>[a-zA-Z][a-zA-Z0-9_-]*)\."
    r"(?P<var>[a-zA-Z_][a-zA-Z0-9_]*)"
    r"\[(?P<idx>i|i-1|last|\d+)\]"
    r"(?P<fields>(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)$"
)


class ResolutionError(Exception):
    """Raised when a `$var` reference cannot be resolved at dispatch time."""


@dataclass(frozen=True)
class ResolvedInput:
    """One resolved input slot.

    `value` is the raw Python object — either a Pydantic Value model
    instance (when the entire variable is consumed) or a primitive
    (when a field path was applied). `present` is True iff the variable
    was produced upstream; for optional inputs that weren't produced,
    `present` is False and `value` is None.
    """

    name: str
    """Input slot name (with the `?` suffix stripped)."""

    optional: bool
    value: object | None
    present: bool


def _strip_optional_suffix(name: str) -> tuple[str, bool]:
    if name.endswith("?"):
        return name[:-1], True
    return name, False


def _parse_ref(ref: str) -> tuple[str, list[str]]:
    """Returns (var_name, field_path). Raises ResolutionError if malformed."""
    m = _VAR_REF_RE.match(ref.strip())
    if not m:
        raise ResolutionError(f"malformed variable reference {ref!r}")
    var_name = m.group(1)
    field_chain = m.group(2)
    fields = field_chain.lstrip(".").split(".") if field_chain else []
    return var_name, [f for f in fields if f]


def _read_envelope(path: Path) -> Envelope | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if not raw.strip():
        return None
    return Envelope.model_validate_json(raw)


def _materialise_value(envelope: Envelope) -> BaseModel:
    """Hydrate the envelope's `value` dict back into the type's `Value`
    Pydantic model so field access and downstream rendering get a typed
    object instead of a raw dict."""
    type_obj = get_type(envelope.type)
    return type_obj.Value.model_validate(envelope.value)


def _walk_field_path(value: object, fields: list[str], ref: str) -> object:
    cursor: Any = value
    for field in fields:
        if isinstance(cursor, BaseModel):
            if field not in type(cursor).model_fields:
                raise ResolutionError(
                    f"reference {ref!r}: type {type(cursor).__name__} has no field {field!r}"
                )
            cursor = getattr(cursor, field)
        elif isinstance(cursor, dict):
            if field not in cursor:
                raise ResolutionError(f"reference {ref!r}: dict has no key {field!r}")
            cursor = cursor[field]
        else:
            raise ResolutionError(
                f"reference {ref!r}: cannot access field {field!r} on "
                f"non-model value of type {type(cursor).__name__}"
            )
    return cursor


def _highest_loop_iteration(
    *, job_slug: str, loop_id: str, var_name: str, root: Path
) -> int | None:
    """Find the highest iteration the loop body has produced for
    *var_name*. Used for ``[last]`` resolution outside the loop."""
    safe = paths._safe_loop_id(loop_id)
    pattern = f"loop_{safe}_{var_name}_*.json"
    matches = list(paths.variables_dir(job_slug, root=root).glob(pattern))
    if not matches:
        return None
    suffix_re = re.compile(rf"loop_{re.escape(safe)}_{re.escape(var_name)}_(\d+)\.json")
    indices = [int(m.group(1)) for f in matches if (m := suffix_re.match(f.name))]
    return max(indices) if indices else None


def resolve_node_inputs(
    *,
    node: ArtifactNode,
    workflow: Workflow,
    job_slug: str,
    root: Path,
    loop_id: str | None = None,
    iteration: int | None = None,
) -> dict[str, ResolvedInput]:
    """Resolve every input slot on `node` against the engine's variable
    store on disk. Returns a dict keyed by input name (with `?` stripped).

    When ``loop_id`` and ``iteration`` are set, references to ``[i]``
    resolve to that iteration; ``[i-1]`` to the previous one (None on
    iteration 0).

    Raises ``ResolutionError`` only when a *required* input cannot be
    resolved. Missing optional inputs land as ``ResolvedInput(present=False)``.
    """
    resolved: dict[str, ResolvedInput] = {}
    for input_name, ref in node.inputs.items():
        slot_name, optional = _strip_optional_suffix(input_name)

        envelope = _read_loop_or_plain_envelope(
            ref=ref,
            job_slug=job_slug,
            root=root,
            current_iteration=iteration,
        )
        if envelope is None:
            if optional:
                resolved[slot_name] = ResolvedInput(
                    name=slot_name, optional=True, value=None, present=False
                )
                continue
            raise ResolutionError(
                f"node {node.id!r}: required input {slot_name!r} references "
                f"{ref!r} which has not been produced"
            )

        value: object = _materialise_value(envelope)
        # Apply field path if present (extracted by the helper that read
        # the envelope, embedded in the parsed ref).
        fields = _field_path_for_ref(ref)
        if fields:
            value = _walk_field_path(value, fields, ref)
        resolved[slot_name] = ResolvedInput(
            name=slot_name, optional=optional, value=value, present=True
        )
    return resolved


def _read_loop_or_plain_envelope(
    *,
    ref: str,
    job_slug: str,
    root: Path,
    current_iteration: int | None,
):
    """Read the envelope a reference points at — loop-indexed or plain.

    Returns the Envelope or None if the variable hasn't been produced.
    """
    text = ref.strip()
    m_loop = _LOOP_VAR_REF_RE.match(text)
    if m_loop is not None:
        loop_id = m_loop.group("loop_id")
        var_name = m_loop.group("var")
        idx_form = m_loop.group("idx")
        idx = _resolve_loop_index(idx_form, current_iteration, job_slug, loop_id, var_name, root)
        if idx is None:
            return None
        path = paths.loop_variable_envelope_path(job_slug, loop_id, var_name, idx, root=root)
        return _read_envelope(path)

    m_plain = _VAR_REF_RE.match(text)
    if m_plain is not None:
        var_name = m_plain.group(1)
        return _read_envelope(paths.variable_envelope_path(job_slug, var_name, root=root))
    raise ResolutionError(f"malformed variable reference {ref!r}")


def _field_path_for_ref(ref: str) -> list[str]:
    text = ref.strip()
    m_loop = _LOOP_VAR_REF_RE.match(text)
    if m_loop is not None:
        chain = m_loop.group("fields") or ""
    else:
        m_plain = _VAR_REF_RE.match(text)
        chain = m_plain.group(2) if m_plain else ""
    return [f for f in chain.lstrip(".").split(".") if f]


def _resolve_loop_index(
    idx_form: str,
    current_iteration: int | None,
    job_slug: str,
    loop_id: str,
    var_name: str,
    root: Path,
) -> int | None:
    if idx_form == "i":
        return current_iteration
    if idx_form == "i-1":
        if current_iteration is None or current_iteration <= 0:
            return None
        return current_iteration - 1
    if idx_form == "last":
        return _highest_loop_iteration(
            job_slug=job_slug, loop_id=loop_id, var_name=var_name, root=root
        )
    return int(idx_form)
