"""Prompt assembly for Hammock v1 nodes.

Per design-patch §1.4 and §1.7. Builds the markdown prompt the agent
receives by stitching together:

1. A node header (id + description if any).
2. Resolved inputs — each rendered via its variable type's
   ``render_for_consumer``.
3. Output declarations — each rendered via its variable type's
   ``render_for_producer``.

Nothing in this module knows JOB_DIR-vs-cwd, PR protocols, or schema-hint
strings. Those are owned by individual variable types.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.v1.resolver import ResolvedInput
from shared.v1.types.registry import get_type
from shared.v1.workflow import ArtifactNode, Workflow


@dataclass(frozen=True)
class _PromptCtx:
    var_name: str
    job_dir: Path
    loop_id: str | None = None
    iteration: int | None = None

    def expected_path(self) -> Path:
        """Mirrors `engine.v1.artifact._NodeContext.expected_path` so the
        prompt fragment and the post-actor `produce` agree on the file
        path. Loop-aware: produces the indexed path when running inside
        a loop body."""
        if self.loop_id is not None and self.iteration is not None:
            from shared.v1 import paths as _paths

            slug = self.job_dir.name
            root = self.job_dir.parent.parent
            return _paths.loop_variable_envelope_path(
                slug, self.loop_id, self.var_name, self.iteration, root=root
            )
        return self.job_dir / "variables" / f"{self.var_name}.json"


@dataclass(frozen=True)
class OutputSlot:
    """One declared output of a node — slot name (with `?` suffix stripped),
    workflow-level variable name, and its type's `name`."""

    slot_name: str
    optional: bool
    var_name: str
    type_name: str


def collect_output_slots(node: ArtifactNode, workflow: Workflow) -> list[OutputSlot]:
    """Translate a node's declared outputs into typed slots."""
    slots: list[OutputSlot] = []
    for output_name, ref in node.outputs.items():
        slot_name = output_name[:-1] if output_name.endswith("?") else output_name
        optional = output_name.endswith("?")
        # ref is `$var_name` (T1 — no field paths on outputs)
        var_name = ref.lstrip("$").split(".", 1)[0]
        if var_name not in workflow.variables:
            raise ValueError(
                f"node {node.id!r} output {slot_name!r} references undeclared "
                f"variable ${var_name!r}; validator should have caught this"
            )
        type_name = workflow.variables[var_name].type
        slots.append(
            OutputSlot(
                slot_name=slot_name,
                optional=optional,
                var_name=var_name,
                type_name=type_name,
            )
        )
    return slots


def build_prompt(
    *,
    node: ArtifactNode,
    workflow: Workflow,
    inputs: dict[str, ResolvedInput],
    job_dir: Path,
    loop_id: str | None = None,
    iteration: int | None = None,
) -> str:
    """Assemble the full markdown prompt for `node`.

    When the node runs inside a loop, pass ``loop_id`` and ``iteration``
    so the per-output `render_for_producer` includes the loop-indexed
    output path."""
    parts: list[str] = []
    parts.append(f"# Node: {node.id}")
    parts.append("")
    parts.append(
        "You are an agent acting on a Hammock workflow. Read each input "
        "section. Produce each output as a JSON file at the path indicated "
        "in that section's instructions. Do not produce any other files. "
        "Do not write anything to the working directory."
    )
    parts.append("")

    # Inputs ------------------------------------------------------------
    if inputs:
        parts.append("## Inputs")
        parts.append("")
        for slot_name, slot in inputs.items():
            if not slot.present:
                if slot.optional:
                    parts.append(
                        f"### Input `{slot_name}` (optional, not produced)"
                    )
                    parts.append("")
                    parts.append(
                        "(no upstream value yet — proceed without it)"
                    )
                    parts.append("")
                continue
            # Walk back from the value to its variable type. The value can
            # be either a Pydantic model (full variable) or a primitive
            # (field-access result). For primitives we render plainly.
            from pydantic import BaseModel

            if isinstance(slot.value, BaseModel):
                type_name = _type_name_from_value(slot.value, workflow)
                if type_name:
                    type_obj = get_type(type_name)
                    ctx = _PromptCtx(var_name=slot_name, job_dir=job_dir)
                    rendered = type_obj.render_for_consumer(
                        type_obj.Decl(), slot.value, ctx
                    )
                    parts.append(rendered)
                    parts.append("")
                    continue
            # Fallback (e.g., field-access primitive): render plainly.
            parts.append(f"### Input `{slot_name}`")
            parts.append("")
            parts.append(f"```\n{slot.value}\n```")
            parts.append("")

    # Outputs -----------------------------------------------------------
    output_slots = collect_output_slots(node, workflow)
    if output_slots:
        parts.append("## Outputs")
        parts.append("")
        for slot in output_slots:
            type_obj = get_type(slot.type_name)
            ctx = _PromptCtx(
                var_name=slot.var_name,
                job_dir=job_dir,
                loop_id=loop_id,
                iteration=iteration,
            )
            rendered = type_obj.render_for_producer(type_obj.Decl(), ctx)
            parts.append(rendered)
            if slot.optional:
                parts.append(
                    f"_Output `{slot.slot_name}` is optional. Skip producing "
                    "the file if the work this output represents was not needed._"
                )
            parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _type_name_from_value(value: object, workflow: Workflow) -> str | None:
    """Look up a Pydantic model's declared type name by matching it
    against the registry's `Value` classes referenced in the workflow."""
    from pydantic import BaseModel

    if not isinstance(value, BaseModel):
        return None
    for var_name, spec in workflow.variables.items():
        try:
            t = get_type(spec.type)
        except Exception:
            continue
        if isinstance(value, t.Value):
            del var_name  # reuse loop var
            return spec.type
    return None
