"""Unit tests for the T6 variable types: impl-spec, impl-plan, summary.

Each follows the same produce/render contract as T1's design-spec; we
test the key bits — the typed Value validates, produce reads JSON from
expected_path, and the impl-plan exposes a `count` field walkable by
the predicate's field-path resolver."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from shared.v1.types.impl_plan import (
    ImplPlanStage,
    ImplPlanType,
    ImplPlanValue,
)
from shared.v1.types.impl_spec import ImplSpecType
from shared.v1.types.protocol import VariableTypeError
from shared.v1.types.registry import REGISTRY, known_type_names
from shared.v1.types.summary import SummaryType, SummaryValue


@dataclass
class FakeNodeCtx:
    var_name: str
    job_dir: Path

    def expected_path(self) -> Path:
        return self.job_dir / f"{self.var_name}.json"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_contains_t6_types() -> None:
    names = set(known_type_names())
    assert {"impl-spec", "impl-plan", "summary"}.issubset(names)
    assert isinstance(REGISTRY["impl-plan"], ImplPlanType)
    assert isinstance(REGISTRY["impl-spec"], ImplSpecType)
    assert isinstance(REGISTRY["summary"], SummaryType)


# ---------------------------------------------------------------------------
# impl-spec
# ---------------------------------------------------------------------------


def test_impl_spec_produce_roundtrip(tmp_path: Path) -> None:
    payload = {
        "title": "Refactor X",
        "overview": "Two-line overview that fits the schema.",
        "components": ["mod_a.py", "mod_b.py"],
        "interfaces": ["fn(x: int) -> int"],
        "edge_cases": ["empty input"],
        "document": "## Impl spec\n\nRefactor X across mod_a, mod_b.",
    }
    p = tmp_path / "impl_spec.json"
    p.write_text(json.dumps(payload))

    t = ImplSpecType()
    val = t.produce(t.Decl(), FakeNodeCtx(var_name="impl_spec", job_dir=tmp_path))
    assert val.title == "Refactor X"
    assert val.components == ["mod_a.py", "mod_b.py"]


def test_impl_spec_produce_rejects_invalid_schema(tmp_path: Path) -> None:
    (tmp_path / "impl_spec.json").write_text(json.dumps({"title": ""}))
    t = ImplSpecType()
    with pytest.raises(VariableTypeError, match="schema invalid"):
        t.produce(t.Decl(), FakeNodeCtx(var_name="impl_spec", job_dir=tmp_path))


# ---------------------------------------------------------------------------
# impl-plan — count drives count-loop dispatch
# ---------------------------------------------------------------------------


def test_impl_plan_value_carries_count_and_stages() -> None:
    val = ImplPlanValue(
        count=2,
        stages=[
            ImplPlanStage(name="stage-0", description="d0"),
            ImplPlanStage(name="stage-1", description="d1"),
        ],
        document="## Plan\n\nTwo stages.",
    )
    assert val.count == 2
    assert len(val.stages) == 2


def test_impl_plan_count_field_walkable_by_predicate(tmp_path: Path) -> None:
    """Engine resolves `$impl-plan-loop.impl_plan[last].count` by
    materialising the envelope's value into the typed model and walking
    the field path. Confirm the field is reachable via Pydantic
    introspection (not via raw dict access)."""
    from engine.v1.predicate import _walk_field_path

    val = ImplPlanValue(count=3, stages=[], document="## Plan\n\n.")
    walked = _walk_field_path(val, ["count"], "$test.field")
    assert walked == 3


def test_impl_plan_produce_rejects_negative_count(tmp_path: Path) -> None:
    (tmp_path / "impl_plan.json").write_text(json.dumps({"count": -1, "stages": []}))
    t = ImplPlanType()
    with pytest.raises(VariableTypeError, match="schema invalid"):
        t.produce(t.Decl(), FakeNodeCtx(var_name="impl_plan", job_dir=tmp_path))


def test_impl_plan_produce_zero_count_ok(tmp_path: Path) -> None:
    (tmp_path / "impl_plan.json").write_text(
        json.dumps({"count": 0, "stages": [], "document": "## Plan\n\n."})
    )
    t = ImplPlanType()
    val = t.produce(t.Decl(), FakeNodeCtx(var_name="impl_plan", job_dir=tmp_path))
    assert val.count == 0


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def test_summary_produce_roundtrip(tmp_path: Path) -> None:
    payload = {
        "text": "Done. Fixed bug, added tests.",
        "pr_urls": ["https://github.com/o/r/pull/1"],
        "document": "## Summary\n\nDone.",
    }
    (tmp_path / "summary.json").write_text(json.dumps(payload))
    t = SummaryType()
    val = t.produce(t.Decl(), FakeNodeCtx(var_name="summary", job_dir=tmp_path))
    assert val.text.startswith("Done.")
    assert val.pr_urls == ["https://github.com/o/r/pull/1"]


def test_summary_renders_pr_urls(tmp_path: Path) -> None:
    val = SummaryValue(text="ok", pr_urls=["u1", "u2"], document="## Summary\n\nok.")
    t = SummaryType()
    rendered = t.render_for_consumer(
        t.Decl(),
        val,
        type("FPCtx", (), {"var_name": "summary", "job_dir": tmp_path})(),
    )
    assert "u1" in rendered and "u2" in rendered
