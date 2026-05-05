"""`list[T]` parametric wrapper — produced automatically by count loops.

Per design-patch §1.4 + §5.1:

- The engine fans-in a count loop's body output via ``[*]`` into a
  ``list[T]`` aggregate.
- The wrapper has no per-variable ``Decl`` and no ``produce`` (the engine
  writes the aggregated envelope directly when projecting loop outputs).
- ``Value`` is just a ``list`` of inner-type values; we model it as a
  Pydantic ``RootModel`` so envelope validation Just Works.
- ``render_for_consumer`` renders the list by delegating to the inner
  type for each element.

There is one ``ListType`` instance per inner type, built lazily by
``registry.get_type('list[<inner>]')`` and cached.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, RootModel

from shared.v1.types.protocol import (
    FormSchema,
    NodeContext,
    PromptContext,
    VariableTypeError,
)


class ListDecl(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _make_list_value_model(inner_value_cls: type[BaseModel]) -> type[RootModel]:
    """Return a Pydantic RootModel whose root is ``list[inner_value_cls]``.

    Built per inner type so envelope validation (`Value.model_validate`)
    runs the inner Pydantic checks per element."""

    class _ListValue(RootModel[list[inner_value_cls]]):  # type: ignore[valid-type]
        pass

    _ListValue.__name__ = f"List_{inner_value_cls.__name__}"
    return _ListValue


class ListType:
    """Wrapper type for ``list[T]``. Constructed by the registry."""

    Decl: ClassVar[type[ListDecl]] = ListDecl

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.name = f"list[{inner.name}]"
        self.Value = _make_list_value_model(inner.Value)

    def produce(self, decl: ListDecl, ctx: NodeContext) -> Any:
        # Engine populates list envelopes directly when projecting loop
        # outputs; no actor produces a list[T] via the type protocol.
        raise VariableTypeError(
            f"{self.name}: produce() is engine-derived (count-loop "
            "projection), not actor-driven"
        )

    def render_for_producer(self, decl: ListDecl, ctx: PromptContext) -> str:
        return (
            f"### Output `{ctx.var_name}` ({self.name})\n\n"
            "Engine-derived list output — no producer prompt fragment."
        )

    def render_for_consumer(
        self, decl: ListDecl, value: Any, ctx: PromptContext
    ) -> str:
        from dataclasses import dataclass as _dataclass

        @_dataclass
        class _ElemCtx:
            var_name: str
            job_dir: Any

        items = value.root if hasattr(value, "root") else list(value)
        if not items:
            return f"### Input `{ctx.var_name}` ({self.name})\n\n(empty)\n"
        lines = [f"### Input `{ctx.var_name}` ({self.name})\n"]
        inner_decl = self.inner.Decl()
        for k, item in enumerate(items):
            elem_ctx = _ElemCtx(
                var_name=f"{ctx.var_name}[{k}]", job_dir=ctx.job_dir
            )
            lines.append(self.inner.render_for_consumer(inner_decl, item, elem_ctx))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def form_schema(self, decl: ListDecl) -> FormSchema | None:
        return None
