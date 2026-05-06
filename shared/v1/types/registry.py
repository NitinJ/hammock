"""Closed-set variable type registry for Hammock v1.

Per design-patch §1.3. Adding a new type means importing its module here
and registering it. Types referenced in YAML's `variables:` block must
appear in this registry; the validator enforces that.
"""

from __future__ import annotations

import re
from typing import Any

from shared.v1.types.bug_report import BugReportType
from shared.v1.types.design_spec import DesignSpecType
from shared.v1.types.impl_plan import ImplPlanType
from shared.v1.types.impl_spec import ImplSpecType
from shared.v1.types.job_request import JobRequestType
from shared.v1.types.list_wrapper import ListType
from shared.v1.types.pr import PRType
from shared.v1.types.pr_review_verdict import PRReviewVerdictType
from shared.v1.types.review_verdict import ReviewVerdictType
from shared.v1.types.summary import SummaryType


def _build_registry() -> dict[str, Any]:
    """Eager registry build — every type is instantiated once at import.
    Types are stateless; sharing the instance is fine."""
    types: list[Any] = [
        JobRequestType(),
        BugReportType(),
        DesignSpecType(),
        ImplSpecType(),
        ImplPlanType(),
        ReviewVerdictType(),
        PRType(),
        PRReviewVerdictType(),
        SummaryType(),
    ]
    return {t.name: t for t in types}


REGISTRY: dict[str, Any] = _build_registry()


# Cache of synthesised ``list[T]`` types so identity comparisons work.
_LIST_TYPE_CACHE: dict[str, ListType] = {}


_LIST_NAME_RE = re.compile(r"^list\[(?P<inner>[a-zA-Z][a-zA-Z0-9_-]*)\]$")


class UnknownVariableType(KeyError):
    """Raised when YAML or runtime references a type not in the registry."""


def is_known_type(name: str) -> bool:
    """True iff ``name`` is a registered base type or a ``list[<known>]`` form."""
    if name in REGISTRY:
        return True
    m = _LIST_NAME_RE.match(name)
    return bool(m is not None and m.group("inner") in REGISTRY)


def get_type(name: str) -> Any:
    """Look up a registered type by name.

    Recognises ``list[<inner>]`` parametric form (per design-patch §1.4)
    and returns a cached ``ListType`` wrapping the inner registered type.

    Raises ``UnknownVariableType`` (a ``KeyError``) when the name isn't
    registered — the validator catches this at workflow-load time."""
    if name in REGISTRY:
        return REGISTRY[name]
    m = _LIST_NAME_RE.match(name)
    if m is not None:
        inner_name = m.group("inner")
        if inner_name not in REGISTRY:
            raise UnknownVariableType(
                f"variable type {name!r}: inner type {inner_name!r} is not "
                f"registered. Known types: {sorted(REGISTRY.keys())}"
            )
        cached = _LIST_TYPE_CACHE.get(name)
        if cached is None:
            cached = ListType(REGISTRY[inner_name])
            _LIST_TYPE_CACHE[name] = cached
        return cached
    raise UnknownVariableType(
        f"variable type {name!r} is not registered. Known types: {sorted(REGISTRY.keys())}"
    )


def known_type_names() -> list[str]:
    return sorted(REGISTRY.keys())
