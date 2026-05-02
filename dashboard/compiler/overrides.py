"""Modify-only deep-merge for per-project job-template overrides.

Per design doc § Plan Compiler § Override merge semantics — v0 rule:

- Override may **modify** any field of any existing stage (matched by ``id``).
- Override may NOT add, remove, or reorder stages.
- Override stage ids are a subset of the global template's stage ids.

The merge is a deep replacement: object fields replace recursively, lists
are replaced wholesale (no list-merging — semantics too ambiguous).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OverrideFailure:
    """A single override-merge violation."""

    kind: str  # "add_stage" | "remove_stage" | "reorder" | "unknown_id" | "structural"
    stage_id: str | None
    message: str


def merge_overrides(
    base: dict[str, Any],
    override: Any,
) -> tuple[dict[str, Any], list[OverrideFailure]]:
    """Deep-merge *override* into *base* per modify-only semantics.

    *override* is typed as ``Any`` because it comes from ``yaml.safe_load``;
    we accept whatever the user wrote and validate at runtime.

    Returns ``(merged_template, failures)``. If ``failures`` is non-empty,
    *merged_template* is the unchanged *base*.
    """
    if override is None:
        return base, []

    if not isinstance(override, dict):
        return base, [
            OverrideFailure("structural", None, "override must be a YAML mapping at top level")
        ]

    base_stages = base.get("stages", [])
    override_stages = override.get("stages", [])

    if not isinstance(base_stages, list):
        return base, [OverrideFailure("structural", None, "base template has no 'stages' list")]
    if not isinstance(override_stages, list):
        return base, [OverrideFailure("structural", None, "override 'stages' must be a list")]

    failures: list[OverrideFailure] = []
    base_ids: list[str] = []
    base_by_id: dict[str, dict[str, Any]] = {}
    for s in base_stages:
        if not isinstance(s, dict) or "id" not in s:
            return base, [OverrideFailure("structural", None, "base stage missing 'id'")]
        sid = str(s["id"])
        base_ids.append(sid)
        base_by_id[sid] = s

    seen_override_ids: list[str] = []
    for s in override_stages:
        if not isinstance(s, dict) or "id" not in s:
            failures.append(OverrideFailure("structural", None, "override stage missing 'id'"))
            continue
        sid = str(s["id"])
        seen_override_ids.append(sid)
        if sid not in base_by_id:
            failures.append(
                OverrideFailure(
                    "unknown_id",
                    sid,
                    f"override references unknown stage id {sid!r}; "
                    f"v0 overrides may only modify existing stages, not add new ones",
                )
            )

    # Detect reorder: override ids that ARE in base must appear in the same
    # relative order as base.
    valid_override_ids = [sid for sid in seen_override_ids if sid in base_by_id]
    base_index = {sid: i for i, sid in enumerate(base_ids)}
    last_idx = -1
    for sid in valid_override_ids:
        idx = base_index[sid]
        if idx <= last_idx:
            failures.append(
                OverrideFailure(
                    "reorder",
                    sid,
                    "override reorders stages; v0 forbids reorder. "
                    "Override stage ids must appear in the same order as the base template.",
                )
            )
            break
        last_idx = idx

    if failures:
        return base, failures

    # Apply modify-only deep merge.
    override_by_id = {str(s["id"]): s for s in override_stages if isinstance(s, dict) and "id" in s}
    merged_stages: list[dict[str, Any]] = []
    for s in base_stages:
        sid = str(s["id"])
        if sid in override_by_id:
            merged_stages.append(_deep_merge(s, override_by_id[sid]))
        else:
            merged_stages.append(s)

    merged = dict(base)
    merged["stages"] = merged_stages
    # Allow override to set top-level fields (description, etc.) via deep-merge,
    # but NOT to redefine stages — already handled above.
    for k, v in override.items():
        if k == "stages":
            continue
        merged[k] = (
            _deep_merge(base.get(k, {}), v)
            if isinstance(v, dict) and isinstance(base.get(k), dict)
            else v
        )
    return merged, []


def _deep_merge(base: Any, override: Any) -> Any:
    """Recursive dict merge: object fields replace recursively; lists replaced wholesale.

    *base* and *override* are typed ``Any`` because the merge walks arbitrary
    YAML-derived structures.
    """
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    out: dict[Any, Any] = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
