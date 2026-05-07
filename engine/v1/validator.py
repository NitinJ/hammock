"""Static workflow validator — design-patch §4.

Runs at workflow-load time and refuses to start a job whose workflow is
structurally incoherent. Returns a list of `ValidationFinding` objects;
empty list = workflow is valid. Hard-fail discipline — every finding is
an error.

T1 scope: artifact-only nodes, no loops, no human actor handling, no
optional/Maybe rules. Future stages add to the check set as we add
capabilities (per IMPL patch §5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from shared.v1.types.registry import REGISTRY, is_known_type
from shared.v1.workflow import ArtifactNode, CodeNode, LoopNode, Workflow

_VAR_REF_RE = re.compile(r"^\$([a-zA-Z][a-zA-Z0-9_-]*)(?:\.([a-zA-Z_][a-zA-Z0-9_]*))*$")
_LOOP_REF_RE = re.compile(
    r"^\$(?P<loop_id>[a-zA-Z][a-zA-Z0-9_-]*)\."
    r"(?P<var>[a-zA-Z_][a-zA-Z0-9_]*)"
    r"\[(?:i|i-1|last|\d+)\]"
    r"(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*$"
)


@dataclass(frozen=True)
class ValidationFinding:
    node_id: str | None
    """Node where the problem was found, or None for workflow-level issues."""

    message: str


class WorkflowValidationError(Exception):
    """Raised by `assert_valid` when findings are non-empty."""

    def __init__(self, findings: list[ValidationFinding]) -> None:
        self.findings = findings
        super().__init__(self._format(findings))

    @staticmethod
    def _format(findings: list[ValidationFinding]) -> str:
        if not findings:
            return "no findings"
        lines = [f"{len(findings)} validation finding(s):"]
        for f in findings:
            prefix = f"node {f.node_id!r}" if f.node_id else "workflow"
            lines.append(f"  {prefix}: {f.message}")
        return "\n".join(lines)


def validate(workflow: Workflow) -> list[ValidationFinding]:
    """Run every v1 check. Empty list = valid."""
    findings: list[ValidationFinding] = []
    findings.extend(_check_variable_types_registered(workflow))
    findings.extend(_check_node_ids_unique(workflow))
    findings.extend(_check_after_references_exist(workflow))
    findings.extend(_check_no_cycles(workflow))
    findings.extend(_check_input_references(workflow))
    findings.extend(_check_output_references(workflow))
    findings.extend(_check_single_producer_per_variable(workflow))
    findings.extend(_check_loop_until_predicate_types(workflow))
    return findings


def assert_valid(workflow: Workflow) -> None:
    """Convenience: validate and raise if any finding."""
    findings = validate(workflow)
    if findings:
        raise WorkflowValidationError(findings)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_variable_types_registered(workflow: Workflow) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for var_name, spec in workflow.variables.items():
        if not is_known_type(spec.type):
            findings.append(
                ValidationFinding(
                    node_id=None,
                    message=(
                        f"variable {var_name!r} has unknown type {spec.type!r}. "
                        f"Known types: {sorted(REGISTRY.keys())} (also "
                        f"`list[<known>]` forms)"
                    ),
                )
            )
    return findings


def _check_node_ids_unique(workflow: Workflow) -> list[ValidationFinding]:
    seen: set[str] = set()
    findings: list[ValidationFinding] = []
    for node in workflow.nodes:
        if node.id in seen:
            findings.append(
                ValidationFinding(
                    node_id=node.id,
                    message=f"duplicate node id {node.id!r}",
                )
            )
        seen.add(node.id)
    return findings


def _check_after_references_exist(workflow: Workflow) -> list[ValidationFinding]:
    node_ids = {n.id for n in workflow.nodes}
    findings: list[ValidationFinding] = []
    for node in workflow.nodes:
        for after in node.after:
            if after not in node_ids:
                findings.append(
                    ValidationFinding(
                        node_id=node.id,
                        message=f"`after:` references unknown node {after!r}",
                    )
                )
    return findings


def _check_no_cycles(workflow: Workflow) -> list[ValidationFinding]:
    """T1 has no loops, so any cycle (via `after:`) is an error."""
    graph: dict[str, list[str]] = {n.id: list(n.after) for n in workflow.nodes}
    # Standard 3-colour DFS for cycle detection.
    WHITE, GRAY, BLACK = 0, 1, 2
    colour = dict.fromkeys(graph, WHITE)
    findings: list[ValidationFinding] = []

    def visit(node_id: str, path: list[str]) -> bool:
        if colour[node_id] == GRAY:
            cycle_path = " -> ".join([*path, node_id])
            findings.append(
                ValidationFinding(
                    node_id=node_id,
                    message=f"cycle detected: {cycle_path}",
                )
            )
            return True
        if colour[node_id] == BLACK:
            return False
        colour[node_id] = GRAY
        for parent in graph.get(node_id, []):
            if parent in colour and visit(parent, [*path, node_id]):
                return True
        colour[node_id] = BLACK
        return False

    for n in graph:
        if colour[n] == WHITE:
            visit(n, [])
    return findings


def _parse_var_ref(ref: str) -> str | None:
    """Extract the variable name from any v1 reference form.

    Accepts both ``$var[.field...]`` and the loop-indexed
    ``$loop-id.var[idx][.field...]`` form. Returns the *variable name*
    (the second segment for loop refs, the first segment for plain refs)
    or None if the reference is malformed."""
    text = ref.strip()
    m_loop = _LOOP_REF_RE.match(text)
    if m_loop is not None:
        return m_loop.group("var")
    m_plain = _VAR_REF_RE.match(text)
    if m_plain is not None:
        return m_plain.group(1)
    return None


def _strip_optional_suffix(name: str) -> str:
    """`prior_review?` → `prior_review`; bare names returned as-is."""
    return name[:-1] if name.endswith("?") else name


def _walk_nodes(workflow: Workflow) -> list:
    """Flatten top-level + loop-body nodes for whole-workflow checks."""
    out: list = []
    for n in workflow.nodes:
        out.append(n)
        if isinstance(n, LoopNode):
            out.extend(n.body)
    return out


def _check_input_references(workflow: Workflow) -> list[ValidationFinding]:
    """Every input value is a `$variable` or `$loop-id.var[i]` reference.
    The variable must be declared in the workflow's `variables:` block."""
    findings: list[ValidationFinding] = []
    declared = set(workflow.variables.keys())
    for node in _walk_nodes(workflow):
        if not isinstance(node, ArtifactNode | CodeNode):
            continue
        for input_name, ref in node.inputs.items():
            var_name = _parse_var_ref(ref)
            if var_name is None:
                findings.append(
                    ValidationFinding(
                        node_id=node.id,
                        message=(
                            f"input {input_name!r} has malformed reference {ref!r} "
                            f"(expected `$variable[.field]` or "
                            f"`$loop-id.var[idx][.field]`)"
                        ),
                    )
                )
                continue
            if var_name not in declared:
                findings.append(
                    ValidationFinding(
                        node_id=node.id,
                        message=(
                            f"input {input_name!r} references undeclared variable ${var_name!r}"
                        ),
                    )
                )
    return findings


def _check_output_references(workflow: Workflow) -> list[ValidationFinding]:
    """Every output value is a `$var` reference. The referenced variable
    must be declared. The output's *name* may carry a `?` suffix."""
    findings: list[ValidationFinding] = []
    declared = set(workflow.variables.keys())
    for node in _walk_nodes(workflow):
        if not isinstance(node, ArtifactNode | CodeNode):
            continue
        for output_name, ref in node.outputs.items():
            var_name = _parse_var_ref(ref)
            if var_name is None:
                findings.append(
                    ValidationFinding(
                        node_id=node.id,
                        message=(f"output {output_name!r} has malformed reference {ref!r}"),
                    )
                )
                continue
            if var_name not in declared:
                findings.append(
                    ValidationFinding(
                        node_id=node.id,
                        message=(
                            f"output {output_name!r} references undeclared variable ${var_name!r}"
                        ),
                    )
                )
    return findings


def _check_loop_until_predicate_types(workflow: Workflow) -> list[ValidationFinding]:
    """Reject bare-ref ``until:`` predicates whose target is not bool.

    The footgun: ``until: $loop.var[i]`` evaluates the value's
    truthiness. Envelope objects are *always* truthy when present, so
    the loop exits after iteration 1 regardless of what was produced.
    The author meant to compare against a literal — e.g.
    ``until: $loop.var[i].verdict == 'approved'``. We catch this at
    workflow-load so authors learn before submitting a job.

    Limited to ``until`` predicates: ``runs_if: $tests_pr`` (envelope
    presence/absence) is a legitimate truthiness check.
    """
    from engine.v1.predicate import PredicateError, parse_predicate
    from shared.v1.types.registry import UnknownVariableType, get_type
    from shared.v1.workflow import LoopNode

    findings: list[ValidationFinding] = []

    def visit(nodes) -> None:
        for n in nodes:
            if isinstance(n, LoopNode):
                if n.until is not None:
                    f = _check_one_until(n, workflow, parse_predicate, get_type, UnknownVariableType)
                    if f is not None:
                        findings.append(f)
                visit(n.body)

    def _check_one_until(
        loop_node, workflow, parse_predicate, get_type, UnknownVariableType
    ) -> ValidationFinding | None:
        try:
            parsed = parse_predicate(loop_node.until)
        except PredicateError as exc:
            return ValidationFinding(
                node_id=loop_node.id,
                message=f"`until:` predicate {loop_node.until!r} could not be parsed: {exc}",
            )
        if parsed.op is not None:
            # `... == 'literal'` / `... != 'literal'` — comparison
            # always yields bool. No further check needed.
            return None
        ref = parsed.ref
        if ref.var_name not in workflow.variables:
            # Reference to an undeclared variable will be caught by
            # other checks; don't double-report here.
            return None
        type_name = workflow.variables[ref.var_name].type
        try:
            type_obj = get_type(type_name)
        except UnknownVariableType:
            return None
        # Walk the field path on the type's `Value` model to resolve
        # the eventual Python type. If the resolved type is `bool`,
        # the bare-ref form is fine. Otherwise it's the footgun.
        value_model = getattr(type_obj, "Value", None)
        resolved = _resolve_field_type(value_model, ref.field_path)
        if resolved is bool:
            return None
        suggestion = (
            f"  e.g. `until: {loop_node.until}.<field> == 'literal'` "
            f"(see pr-merged-loop in fix-bug for the correct pattern)"
        )
        path_repr = (
            f"$" + (f"{ref.loop_id}." if ref.loop_id else "") + ref.var_name
            + (f"[{ref.index_form}]" if ref.index_form else "")
            + ("." + ".".join(ref.field_path) if ref.field_path else "")
        )
        return ValidationFinding(
            node_id=loop_node.id,
            message=(
                f"`until:` bare-reference predicate {path_repr!r} resolves to "
                f"non-bool type ({type_name!r}). Envelope values are always "
                f"truthy when present, so the loop will exit after iteration 1 "
                f"regardless of what the body produced. Use an explicit "
                f"comparison instead:\n{suggestion}"
            ),
        )

    visit(workflow.nodes)
    return findings


def _resolve_field_type(value_model: object, field_path: list[str]) -> object | None:
    """Walk ``field_path`` on a Pydantic ``Value`` model and return the
    Python type at the end. Returns ``None`` when the walk can't resolve
    (unknown field, dynamic type, etc.) — caller treats unresolved as
    "not bool" so the predicate must use an explicit comparison."""
    from pydantic import BaseModel

    cursor: object | None = value_model
    for field in field_path:
        if not isinstance(cursor, type) or not issubclass(cursor, BaseModel):
            return None
        if field not in cursor.model_fields:
            return None
        annotation = cursor.model_fields[field].annotation
        cursor = annotation
    return cursor


def _check_single_producer_per_variable(
    workflow: Workflow,
) -> list[ValidationFinding]:
    """Each declared variable has at most one producing node.

    Variables produced inside a loop body are still single-producer at
    the *node* level — the loop just runs that producer multiple times.
    Different loop iterations write to different on-disk paths
    (loop_<id>_<var>_<i>.json) so collision is impossible."""
    findings: list[ValidationFinding] = []
    producers: dict[str, list[str]] = {}
    for node in _walk_nodes(workflow):
        if not isinstance(node, ArtifactNode | CodeNode):
            continue
        for ref in node.outputs.values():
            var_name = _parse_var_ref(ref)
            if var_name is None:
                continue
            producers.setdefault(var_name, []).append(node.id)
    for var_name, nodes in producers.items():
        if len(nodes) > 1:
            findings.append(
                ValidationFinding(
                    node_id=None,
                    message=(
                        f"variable ${var_name!r} has multiple producers: {nodes}. "
                        "Single producer per scalar is required."
                    ),
                )
            )
    return findings
