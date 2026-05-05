"""Predicate evaluator for loop ``until`` conditions and node ``runs_if``.

Per design-patch §1.5 + §1.6. v1 grammar is intentionally tiny:

    <ref> [<op> <literal>]

where:

- ``<ref>``      := ``$loop-id.var[i|i-1|last|<int>].field.path`` |
                    ``$variable.field.path``
- ``<op>``       := ``==`` | ``!=``
- ``<literal>``  := ``'string'`` | ``"string"`` | ``true`` | ``false`` | int

Bare reference form (``$tests_pr``): truthy iff the variable is present
and not None (per design-patch §1.6 rule 1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from shared.v1 import paths
from shared.v1.envelope import Envelope
from shared.v1.types.registry import get_type
from shared.v1.workflow import Workflow


class PredicateError(Exception):
    """Raised when a predicate cannot be parsed or evaluated."""


_LOOP_REF_RE = re.compile(
    r"^\$(?P<loop_id>[a-zA-Z][a-zA-Z0-9_-]*)\."
    r"(?P<var>[a-zA-Z_][a-zA-Z0-9_]*)"
    r"\[(?P<idx>i|i-1|last|\d+)\]"
    r"(?:\.(?P<fields>[a-zA-Z_][a-zA-Z0-9_.]*))?$"
)
_PLAIN_REF_RE = re.compile(
    r"^\$(?P<var>[a-zA-Z][a-zA-Z0-9_-]*)"
    r"(?:\.(?P<fields>[a-zA-Z_][a-zA-Z0-9_.]*))?$"
)
_LITERAL_RE = re.compile(
    r"""^\s*(
        '(?P<sq>[^']*)' |
        "(?P<dq>[^"]*)" |
        (?P<bool>true|false) |
        (?P<int>-?\d+)
    )\s*$""",
    re.VERBOSE,
)
_PREDICATE_RE = re.compile(r"^\s*(?P<lhs>\$\S+)\s+(?P<op>==|!=)\s+(?P<rhs>.+)$")


@dataclass(frozen=True)
class ParsedRef:
    loop_id: str | None
    var_name: str
    index_form: str | None  # 'i' | 'i-1' | 'last' | digit string | None
    field_path: list[str]


@dataclass(frozen=True)
class ParsedPredicate:
    ref: ParsedRef
    op: str | None  # '==' / '!=' / None (truthiness)
    literal: object | None


def parse_ref(ref: str) -> ParsedRef:
    text = ref.strip()
    m = _LOOP_REF_RE.match(text)
    if m is not None:
        return ParsedRef(
            loop_id=m.group("loop_id"),
            var_name=m.group("var"),
            index_form=m.group("idx"),
            field_path=m.group("fields").split(".") if m.group("fields") else [],
        )
    m = _PLAIN_REF_RE.match(text)
    if m is not None:
        return ParsedRef(
            loop_id=None,
            var_name=m.group("var"),
            index_form=None,
            field_path=m.group("fields").split(".") if m.group("fields") else [],
        )
    raise PredicateError(f"could not parse reference {ref!r}")


def _parse_literal(text: str) -> object:
    m = _LITERAL_RE.match(text)
    if m is None:
        raise PredicateError(f"could not parse literal {text!r}")
    if m.group("sq") is not None:
        return m.group("sq")
    if m.group("dq") is not None:
        return m.group("dq")
    if m.group("bool") is not None:
        return m.group("bool") == "true"
    if m.group("int") is not None:
        return int(m.group("int"))
    raise PredicateError(f"could not parse literal {text!r}")


def parse_predicate(text: str) -> ParsedPredicate:
    s = text.strip()
    m = _PREDICATE_RE.match(s)
    if m is None:
        return ParsedPredicate(ref=parse_ref(s), op=None, literal=None)
    return ParsedPredicate(
        ref=parse_ref(m.group("lhs")),
        op=m.group("op"),
        literal=_parse_literal(m.group("rhs")),
    )


def _resolve_index(index_form: str, current_iteration: int | None) -> int | None:
    """Translate index form to a concrete iteration index for envelope lookup.

    - 'i' → current_iteration (None if not in a loop context).
    - 'i-1' → current_iteration - 1, None if 0 (first iteration has no prior).
    - 'last' → caller resolves by checking which iterations have envelopes
      on disk; we return a sentinel ``-1`` to signal "find the highest".
    - <digit string> → parsed int.
    """
    if index_form == "i":
        return current_iteration
    if index_form == "i-1":
        if current_iteration is None or current_iteration <= 0:
            return None
        return current_iteration - 1
    if index_form == "last":
        return -1  # sentinel
    return int(index_form)


def _read_loop_envelope(
    *,
    job_slug: str,
    loop_id: str,
    var_name: str,
    iteration: int,
    root: Path,
) -> Envelope | None:
    p = paths.loop_variable_envelope_path(job_slug, loop_id, var_name, iteration, root=root)
    if not p.is_file():
        return None
    return Envelope.model_validate_json(p.read_text())


def _read_plain_envelope(*, job_slug: str, var_name: str, root: Path) -> Envelope | None:
    p = paths.variable_envelope_path(job_slug, var_name, root=root)
    if not p.is_file():
        return None
    return Envelope.model_validate_json(p.read_text())


def _highest_loop_iteration(
    *, job_slug: str, loop_id: str, var_name: str, root: Path
) -> int | None:
    """Find the largest iteration index for which the variable has been
    produced inside the loop. Used by ``[last]``."""
    safe = paths._safe_loop_id(loop_id)
    pattern = f"loop_{safe}_{var_name}_*.json"
    vd = paths.variables_dir(job_slug, root=root)
    matches = sorted(vd.glob(pattern))
    if not matches:
        return None
    suffix_re = re.compile(rf"loop_{re.escape(safe)}_{re.escape(var_name)}_(\d+)\.json")
    indices = []
    for m in matches:
        sm = suffix_re.match(m.name)
        if sm is not None:
            indices.append(int(sm.group(1)))
    return max(indices) if indices else None


def _walk_field_path(value: object, fields: list[str], ref_text: str) -> object:
    cursor = value
    for field in fields:
        if isinstance(cursor, BaseModel):
            if field not in type(cursor).model_fields:
                raise PredicateError(
                    f"reference {ref_text!r}: type {type(cursor).__name__} has no field {field!r}"
                )
            cursor = getattr(cursor, field)
        elif isinstance(cursor, dict):
            if field not in cursor:
                raise PredicateError(f"reference {ref_text!r}: dict has no key {field!r}")
            cursor = cursor[field]
        else:
            raise PredicateError(
                f"reference {ref_text!r}: cannot access field {field!r} on "
                f"non-model value of type {type(cursor).__name__}"
            )
    return cursor


def evaluate(
    text: str,
    *,
    workflow: Workflow,
    job_slug: str,
    root: Path,
    current_iteration: int | None = None,
) -> bool:
    """Evaluate a predicate to a bool. Used by loop until / runs_if.

    Resolution rules per design-patch §1.5:
    - In-loop reference (`$loop-id.var[i]`): reads envelope from
      indexed-variable storage.
    - Out-of-loop reference (`$variable`): reads from the plain variable store.
    - Bare reference truthiness: present and non-None ⇒ True; absent ⇒ False.
    """
    parsed = parse_predicate(text)
    ref = parsed.ref

    # Resolve the value the LHS points at.
    value: object | None
    if ref.loop_id is not None:
        # Loop-scoped reference.
        if ref.index_form is None:
            raise PredicateError(f"loop-scoped reference must have an index: {text!r}")
        idx = _resolve_index(ref.index_form, current_iteration)
        if idx is None:
            # `[i-1]` on iteration 0, or `[i]` outside a loop. Treat as absent.
            value = None
        elif idx == -1:  # 'last' sentinel
            highest = _highest_loop_iteration(
                job_slug=job_slug, loop_id=ref.loop_id, var_name=ref.var_name, root=root
            )
            if highest is None:
                value = None
            else:
                envelope = _read_loop_envelope(
                    job_slug=job_slug,
                    loop_id=ref.loop_id,
                    var_name=ref.var_name,
                    iteration=highest,
                    root=root,
                )
                value = _materialise_envelope_value(envelope) if envelope else None
        else:
            envelope = _read_loop_envelope(
                job_slug=job_slug,
                loop_id=ref.loop_id,
                var_name=ref.var_name,
                iteration=idx,
                root=root,
            )
            value = _materialise_envelope_value(envelope) if envelope else None
    else:
        envelope = _read_plain_envelope(job_slug=job_slug, var_name=ref.var_name, root=root)
        value = _materialise_envelope_value(envelope) if envelope else None

    if value is not None and ref.field_path:
        try:
            value = _walk_field_path(value, ref.field_path, text)
        except PredicateError:
            # Missing nested field on a present value — treat as None for
            # predicate purposes (caller can write `?? false`-style guards
            # if they need stricter behaviour).
            value = None

    # Apply op + literal.
    if parsed.op is None:
        # Bare reference: truthiness.
        if value is None:
            return False
        if isinstance(value, bool):
            return value
        return True

    if parsed.op == "==":
        return _coerce_eq(value, parsed.literal)
    if parsed.op == "!=":
        return not _coerce_eq(value, parsed.literal)
    raise PredicateError(f"unknown op {parsed.op!r}")


def _coerce_eq(left: object, right: object) -> bool:
    """Tolerant equality: compare strings, ints, bools without surprises."""
    if left is None or right is None:
        return left == right
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    return left == right


def _materialise_envelope_value(envelope: Envelope) -> BaseModel | None:
    """Turn an envelope's `value` dict back into the type's `Value` model."""
    try:
        type_obj = get_type(envelope.type)
    except KeyError:
        return None
    return type_obj.Value.model_validate(envelope.value)
