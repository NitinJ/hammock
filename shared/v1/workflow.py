"""Workflow model — the YAML-loadable top-level Hammock structure.

Per `docs/hammock-design-patch.md` §1 and §2. T1 scope: `artifact` kind
nodes only, no loops, no code substrate, no HIL. Future stages will add
`code` and `loop` discriminants here without breaking the existing fields.

Minimal for now — every field beyond what T1 needs is omitted to keep the
model honest. Adding a field is a deliberate decision documented when the
test stage that exercises it lands.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------


class VariableSpec(BaseModel):
    """Workflow-level variable declaration (entry in the `variables:` block).

    Just the type name for now. Per-variable config (`Decl`) is a v1+ extension
    we'll add when a test stage requires it (e.g. T3+ for `pr` decls). See
    design-patch §1.4 for the full contract.
    """

    model_config = ConfigDict(extra="forbid")

    type: str  # one of the registered VariableType names


# ---------------------------------------------------------------------------
# Nodes — discriminated by `kind`
# ---------------------------------------------------------------------------


Actor = Literal["agent", "human", "engine"]


class ArtifactNode(BaseModel):
    """A node whose engine substrate is the job dir only — no worktree, no
    branch, no repo. See design-patch §2.1."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["artifact"]
    actor: Actor
    after: list[str] = Field(default_factory=list)
    runs_if: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    """Input declarations: ``name: $variable_reference``. The reference
    string is resolved by the engine's variable resolver (§1.5). An input
    name suffixed with ``?`` is optional."""

    outputs: dict[str, str] = Field(default_factory=dict)
    """Output declarations: ``name: $variable_reference``. The reference
    points at the workflow-level variable this node's output produces.
    A name suffixed with ``?`` is optional (the node may legitimately
    not produce it)."""

    presentation: dict[str, Any] | None = None
    """For human-actor nodes: title/summary shown on the dashboard."""

    retries: dict[str, int] | None = None
    """Optional ``{ max: N }``. Default: 0 retries."""


class CodeNode(BaseModel):
    """A node whose engine substrate is the job dir + a per-stage worktree
    + a stage branch forked off the job branch. See design-patch §2.1
    and §2.4."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["code"]
    actor: Actor
    after: list[str] = Field(default_factory=list)
    runs_if: str | None = None
    inputs: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    presentation: dict[str, Any] | None = None
    retries: dict[str, int] | None = None


class LoopNode(BaseModel):
    """A control-structure node that iterates a sub-DAG (`body`).

    Per design-patch §2.1 + §5. Has no actor; the engine drives
    iterations directly. ``count`` and ``until`` are mutually exclusive.

    - ``count`` loop: runs the body that many times, indexed 0..count-1.
    - ``until`` loop: runs the body until the predicate evaluates true on
      the current iteration's variables, capped at ``max_iterations``.
    - ``substrate``: ``per-iteration`` (default for count) or ``shared``
      (default for until). Affects whether code-kind body nodes get a
      fresh worktree per iteration or reuse one.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: Literal["loop"]
    after: list[str] = Field(default_factory=list)
    runs_if: str | None = None

    # Exactly one of `count` or `until` must be set.
    count: str | int | None = None
    """``count`` form: literal int or ``$variable.field`` reference."""

    until: str | None = None
    """``until`` form: predicate expression like ``$loop-id.var[i].field == 'literal'``."""

    max_iterations: int | None = None
    """Required for ``until`` loops; optional for ``count``."""

    substrate: Literal["per-iteration", "shared"] | None = None
    """When omitted: ``per-iteration`` for count, ``shared`` for until."""

    body: list[Node] = Field(default_factory=list)
    outputs: dict[str, str] = Field(default_factory=dict)
    """Loop output projection: external_name → ``$loop-id.body_var`` reference.
    For count loops the projection is ``list[T]``; for until loops, scalar T
    (final iteration's value via ``[last]``)."""


# Discriminated union: the validator sees the `kind` field and routes
# to the right model. As of T4: `artifact`, `code`, `loop`.
Node = Annotated[ArtifactNode | CodeNode | LoopNode, Field(discriminator="kind")]


# Forward-ref resolution for LoopNode.body (typing self-reference).
LoopNode.model_rebuild()


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class Workflow(BaseModel):
    """The top-level YAML schema."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    """Schema version, mandatory from Stage 4. The engine refuses to
    load a yaml whose schema_version is missing or higher than the
    currently-supported version (1). Adding this field from day one
    means future schema evolutions can fail loud — the alternative
    (no version field) would silently misinterpret post-evolution
    yamls written against an old hammock."""

    workflow: str
    """Workflow name (used for logging / display)."""

    variables: dict[str, VariableSpec] = Field(default_factory=dict)
    """Workflow-level variables, keyed by variable name."""

    nodes: list[Node]
    """Nodes in declaration order. Execution order is determined by `after:`
    edges, not list order."""
