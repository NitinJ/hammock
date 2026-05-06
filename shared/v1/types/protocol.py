"""VariableType protocol — the contract every typed variable implements.

Per design-patch §1.4. The closed-set v1 types live alongside this module
(`bug_report.py`, `design_spec.py`, `review_verdict.py`, ...) and each
implements the protocol below.

The engine handles generically (no per-type method needed):
- Serialise / deserialise — derived from `Value` Pydantic.
- Field access in predicates (`$var.field`) — Pydantic introspection.
- Truthiness in `runs_if` — present = truthy, absent = falsy.
- List wrapping (`list[T]`) — generic, produced by count loops.
- Optional wrapping (`Maybe[T]`) — generic, marker-driven via `?`.
- `Decl` validation — the `Decl` Pydantic class itself.

Adding a new type = define `Decl`, define `Value`, implement 3-4 methods.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

if TYPE_CHECKING:
    from pydantic import BaseModel


class VariableTypeError(Exception):
    """Raised by `VariableType.produce` when the contract isn't satisfied
    after the actor finishes — the engine treats this as 'node failed
    contract' and surfaces the message to the operator."""


class FormSchema:
    """Form schema returned by `VariableType.form_schema` for human-actor
    nodes.

    v1 minimal: just a list of (field_name, field_type) pairs. The dashboard
    renders generic widgets per field type. Custom widgets per variable type
    are a v1+ extension.
    """

    def __init__(self, fields: list[tuple[str, str]]) -> None:
        self.fields = fields


class NodeContext(Protocol):
    """Engine-owned runtime context the type uses to do its work.

    Per design-patch §1.4 + §2.2. Different fields are populated for
    different node kinds:

    - For `artifact` kind: `var_name`, `job_dir`, `expected_path`.
    - For `code` kind: above + `actor_workdir`, `stage_branch`,
      `base_branch`, `repo` (slug), plus engine helpers (`git_push`,
      `gh_create_pr`, `branch_has_commits`, `latest_commit_subject`,
      `latest_commit_body`).

    The protocol below is intentionally a structural type — concrete
    `NodeContext` implementations are private to each dispatcher
    (`engine.v1.artifact._NodeContext`, `engine.v1.code_dispatch._NodeContext`)
    so type code can rely only on the methods it actually needs.
    """

    var_name: str
    job_dir: Path

    inputs: dict[str, Any]
    """Resolved upstream variable values keyed by input slot name (with
    ``?`` stripped). Populated by the engine before invoking ``produce``
    so human-actor types like ``pr-review-verdict`` can read upstream
    state (the linked PR URL, etc.) directly. For agent / code dispatch
    the engine populates this from `engine/v1/resolver.resolve_node_inputs`;
    for HIL submissions, ``engine/v1/hil.submit_hil_answer`` populates it
    from the same resolver.

    Values are raw: a Pydantic ``Value`` instance for whole-variable
    consumption, or a primitive when the YAML reference applied a field
    path. Optional inputs that were not produced upstream are absent
    from the dict (not present-with-None)."""

    def expected_path(self) -> Path:
        """Where engine stores files for this variable. Engine owns the
        layout; types are path-agnostic except via this method."""
        ...


class PromptContext(Protocol):
    """Read-only context for prompt-rendering methods.

    Carries enough state for `render_for_producer` / `render_for_consumer`
    to produce useful prompt fragments without needing the full NodeContext.
    """

    var_name: str
    job_dir: Path

    def expected_path(self) -> Path:
        """Where the engine will look for this variable's value on disk.
        ``render_for_producer`` uses this to tell the agent the exact
        path to write to — kept in sync with ``NodeContext.expected_path``
        so prompt and produce agree."""
        ...


class VariableType(Protocol):
    """The protocol every closed-registry variable type implements."""

    name: ClassVar[str]
    """Type identity used in YAML (`{ type: <name> }`) and in envelope
    metadata."""

    Decl: ClassVar[type[BaseModel]]
    """Per-variable config schema. The YAML's variable declaration's
    extra fields beyond `type:` are validated against this."""

    Value: ClassVar[type[BaseModel]]
    """The produced value's schema. The engine serialises an instance of
    this into the envelope's `value` field."""

    def produce(self, decl: BaseModel, ctx: NodeContext) -> BaseModel:
        """After the actor finishes the node, produce the typed value.

        Raises `VariableTypeError` if the contract isn't satisfied (file
        missing, schema fails, gh pr create fails, etc.). The engine
        catches and marks the node as having failed.
        """
        ...

    def render_for_producer(self, decl: BaseModel, ctx: PromptContext) -> str:
        """Markdown fragment that goes into the producing node's prompt.
        Tells the actor what they must do to satisfy the contract."""
        ...

    def render_for_consumer(self, decl: BaseModel, value: BaseModel, ctx: PromptContext) -> str:
        """Markdown fragment for consuming nodes. Inlines or summarises
        the upstream value."""
        ...

    def form_schema(self, decl: BaseModel) -> FormSchema | None:
        """For human-actor nodes producing this type. Returns None if the
        type is not human-producible.

        Default-able: types whose Value is a Pydantic model can derive the
        form trivially. Types override for custom widgets."""
        ...
