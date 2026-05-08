"""Outcome assertion helpers for e2e_v1 tests.

Variable-shaped (per design-patch): test asserts that the engine's typed
variables landed correctly on disk, not that arbitrary stage files exist
at arbitrary paths.

Each helper is a pure function of (root, job_slug, workflow). Failure
raises AssertionError with a message that names the missing/violating
variable or node.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from shared.v1 import paths
from shared.v1.envelope import Envelope
from shared.v1.job import JobConfig, JobState, NodeRun, NodeRunState
from shared.v1.types.registry import get_type
from shared.v1.workflow import ArtifactNode, CodeNode, Workflow

OutcomeFn = Callable[[Path, str, Workflow], None]


# ---------------------------------------------------------------------------
# 1. Job state
# ---------------------------------------------------------------------------


def assert_job_completed(root: Path, job_slug: str, workflow: Workflow) -> None:
    cfg_path = paths.job_config_path(job_slug, root=root)
    if not cfg_path.is_file():
        raise AssertionError(f"job config missing at {cfg_path}")
    cfg = JobConfig.model_validate_json(cfg_path.read_text())
    if cfg.state != JobState.COMPLETED:
        raise AssertionError(f"job {job_slug!r} did not reach COMPLETED — saw {cfg.state.value}")


# ---------------------------------------------------------------------------
# 2. Every declared variable that should have been produced was produced
# ---------------------------------------------------------------------------


def assert_all_declared_outputs_produced(root: Path, job_slug: str, workflow: Workflow) -> None:
    """Every required output declared by a top-level (non-loop-body) node
    must have an envelope on disk at the plain path.

    Loop-body outputs land at indexed paths (``loop_<id>_<var>_<i>.json``)
    and are exempt from this check — they're verified via the loop's
    own outputs projection (when present) or via type-specific outcomes
    (e.g. ``pr_envelopes_correspond_to_real_prs`` walks both layouts).

    Optional outputs (``?`` suffix) are exempt regardless of location."""
    from shared.v1.workflow import CodeNode, LoopNode

    # Collect variable names produced INSIDE loop bodies.
    loop_internal_vars: set[str] = set()
    for node in workflow.nodes:
        if not isinstance(node, LoopNode):
            continue
        for body_node in node.body:
            if not isinstance(body_node, ArtifactNode | CodeNode):
                continue
            for ref in body_node.outputs.values():
                loop_internal_vars.add(ref.lstrip("$").split(".", 1)[0])

    # Top-level (non-loop-body) required outputs.
    required_outputs: set[str] = set()
    for node in workflow.nodes:
        if not isinstance(node, ArtifactNode | CodeNode):
            continue
        for output_name, ref in node.outputs.items():
            if output_name.endswith("?"):
                continue
            required_outputs.add(ref.lstrip("$").split(".", 1)[0])

    # Plus loop output projections (those land at plain paths).
    for node in workflow.nodes:
        if not isinstance(node, LoopNode):
            continue
        for external_name in node.outputs:
            required_outputs.add(external_name)

    # Loop-internal variables aren't required at the plain path unless
    # they're projected (covered above).
    required_outputs -= loop_internal_vars - set(
        external for n in workflow.nodes if isinstance(n, LoopNode) for external in n.outputs
    )

    for var_name in required_outputs:
        env_path = paths.variable_envelope_path(job_slug, var_name, root=root)
        if not env_path.is_file():
            raise AssertionError(
                f"declared required output ${var_name!r} missing on disk at {env_path}"
            )


# ---------------------------------------------------------------------------
# 3. Envelopes parse, carry consistent metadata
# ---------------------------------------------------------------------------


def assert_envelopes_well_formed(root: Path, job_slug: str, workflow: Workflow) -> None:
    """Every persisted variable envelope (plain or loop-indexed) is
    JSON-valid, has the right ``type`` field, and the ``value`` payload
    validates against the type's ``Value`` Pydantic model."""
    import re as _re

    vars_dir = paths.variables_dir(job_slug, root=root)
    if not vars_dir.is_dir():
        raise AssertionError(f"variables dir missing at {vars_dir}")

    loop_indexed_re = _re.compile(r"^loop_(.+?)_([a-zA-Z_][a-zA-Z0-9_]*)_(\d+)$")

    for env_path in sorted(vars_dir.glob("*.json")):
        stem = env_path.stem
        m = loop_indexed_re.match(stem)
        if m is not None:
            # Loop-indexed envelope: extract the underlying var name.
            var_name = m.group(2)
        else:
            var_name = stem

        try:
            raw = env_path.read_text()
        except OSError as exc:
            raise AssertionError(f"could not read envelope at {env_path}: {exc}") from exc
        try:
            envelope = Envelope.model_validate_json(raw)
        except Exception as exc:
            raise AssertionError(f"envelope at {env_path} failed schema validation: {exc}") from exc

        if var_name not in workflow.variables:
            raise AssertionError(
                f"envelope for unknown variable {var_name!r} at {env_path} "
                f"(not in workflow variables)"
            )
        declared_type = workflow.variables[var_name].type
        if envelope.type != declared_type:
            raise AssertionError(
                f"envelope at {env_path} declares type {envelope.type!r} but "
                f"workflow variable {var_name!r} is type {declared_type!r}"
            )

        type_obj = get_type(envelope.type)
        try:
            type_obj.Value.model_validate(envelope.value)
        except Exception as exc:
            raise AssertionError(
                f"envelope at {env_path} value payload doesn't satisfy "
                f"{declared_type!r} type schema: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# 4. Every node either SUCCEEDED or correctly SKIPPED
# ---------------------------------------------------------------------------


def assert_all_nodes_succeeded_or_skipped(root: Path, job_slug: str, workflow: Workflow) -> None:
    for node in workflow.nodes:
        state_path = paths.node_state_path(job_slug, node.id, root=root)
        if not state_path.is_file():
            # Could be legitimately not-yet-reached if the node has
            # `runs_if` and its predicate was false; T1 has no runs_if so
            # absence is failure.
            if isinstance(node, ArtifactNode) and node.runs_if is None:
                raise AssertionError(
                    f"node {node.id!r}: state.json not on disk "
                    f"(node never ran but workflow reached COMPLETED)"
                )
            continue
        run = NodeRun.model_validate_json(state_path.read_text())
        if run.state not in {NodeRunState.SUCCEEDED, NodeRunState.SKIPPED}:
            raise AssertionError(
                f"node {node.id!r}: state {run.state.value} (expected SUCCEEDED "
                f"or SKIPPED). last_error={run.last_error!r}"
            )


# ---------------------------------------------------------------------------
# 5. Per-node attempt artefacts present (prompt, stdout, stderr)
# ---------------------------------------------------------------------------


_REQUIRED_ARTEFACTS = ("prompt.md", "chat.jsonl", "stderr.log")


def assert_node_artefacts_present(root: Path, job_slug: str, workflow: Workflow) -> None:
    """For every *agent-actor* node that ran (state.json exists), check
    that the attempt directory has the conventional files (prompt.md,
    chat.jsonl, stderr.log).

    Human-actor and engine-actor nodes don't spawn `claude -p`, so they
    have no attempt-dir artefacts. They're exempt from this check."""
    for node in workflow.nodes:
        if not isinstance(node, ArtifactNode):
            continue
        if node.actor != "agent":
            continue
        state_path = paths.node_state_path(job_slug, node.id, root=root)
        if not state_path.is_file():
            continue
        run = NodeRun.model_validate_json(state_path.read_text())
        attempt_dir = paths.node_attempt_dir(job_slug, node.id, run.attempts, root=root)
        if not attempt_dir.is_dir():
            raise AssertionError(f"node {node.id!r}: attempt dir missing at {attempt_dir}")
        for fname in _REQUIRED_ARTEFACTS:
            f = attempt_dir / fname
            if not f.is_file():
                raise AssertionError(f"node {node.id!r}: missing artefact {fname!r} at {f}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def assert_pr_envelopes_correspond_to_real_prs(
    root: Path, job_slug: str, workflow: Workflow
) -> None:
    """For every persisted ``pr``-type envelope (plain or loop-indexed),
    query GitHub via `gh pr view` to confirm the PR exists.

    Skipped silently if no `pr` envelopes were produced (T1/T2 case)."""
    pr_var_names = {name for name, spec in workflow.variables.items() if spec.type == "pr"}
    if not pr_var_names:
        return

    vars_dir = paths.variables_dir(job_slug, root=root)
    if not vars_dir.is_dir():
        return

    # Walk every envelope on disk and filter by envelope.type, not by
    # filename (filename glob ``loop_*_pr_*.json`` would also match
    # ``loop_*_pr_merge_*.json`` since that var name starts with ``pr_``).
    pr_envelopes: list[Path] = []
    for env_path in vars_dir.glob("*.json"):
        try:
            envelope = Envelope.model_validate_json(env_path.read_text())
        except Exception:
            continue
        if envelope.type == "pr":
            pr_envelopes.append(env_path)

    for env_path in pr_envelopes:
        env = Envelope.model_validate_json(env_path.read_text())
        url = env.value.get("url")
        if not url:
            raise AssertionError(f"pr envelope at {env_path} has no `url` in its value")
        result = subprocess.run(
            ["gh", "pr", "view", url, "--json", "number,state"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"PR at {url} not viewable via gh: {result.stderr.strip()}")


def assert_branches_in_remote(root: Path, job_slug: str, workflow: Workflow) -> None:
    """For workflows that include code-kind nodes, verify the job branch
    and at least one stage branch are present in the remote.

    Skipped for artifact-only workflows."""
    code_nodes = [n for n in workflow.nodes if isinstance(n, CodeNode)]
    if not code_nodes:
        return
    cfg = JobConfig.model_validate_json(paths.job_config_path(job_slug, root=root).read_text())
    if not cfg.repo_slug:
        raise AssertionError("workflow has code nodes but JobConfig.repo_slug is None")

    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{cfg.repo_slug}/branches",
            "--jq",
            ".[].name",
            "--paginate",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"could not list branches via gh api: {result.stderr.strip()}")
    remote_branches = {line.strip() for line in result.stdout.splitlines() if line.strip()}

    job_branch = paths.job_branch_name(job_slug)
    if job_branch not in remote_branches:
        raise AssertionError(
            f"job branch {job_branch!r} not present in remote {cfg.repo_slug!r}. "
            f"Remote branches: {sorted(remote_branches)}"
        )

    expected_stage_prefix = f"hammock/stages/{job_slug}/"
    stage_branches_present = [b for b in remote_branches if b.startswith(expected_stage_prefix)]
    if not stage_branches_present:
        raise AssertionError(
            f"no stage branches under {expected_stage_prefix!r} in remote. "
            f"Got: {sorted(remote_branches)}"
        )


def assert_count_loop_aggregations_have_expected_size(
    root: Path, job_slug: str, workflow: Workflow
) -> None:
    """For every top-level loop with ``[*]`` aggregation outputs, verify
    the projected ``list[T]`` envelope on disk has the expected number of
    elements.

    Only enforced for ``count`` loops with a literal-int count (the
    common T5 case). ``until`` loops and ``$ref``-driven counts are
    skipped — their iteration count isn't statically knowable here.

    Skipped silently for workflows with no loops.
    """
    from shared.v1.workflow import LoopNode as _LoopNode

    for node in workflow.nodes:
        if not isinstance(node, _LoopNode):
            continue
        # Only literal-int count loops, where we know how many iters
        # should have run.
        if not isinstance(node.count, int):
            continue
        expected = node.count
        for external_name, ref in node.outputs.items():
            if not ref.strip().endswith("[*]"):
                continue
            env_path = paths.variable_envelope_path(job_slug, external_name, root=root)
            if not env_path.is_file():
                raise AssertionError(
                    f"loop {node.id!r} declared [*] output {external_name!r} "
                    f"but envelope missing at {env_path}"
                )
            envelope = Envelope.model_validate_json(env_path.read_text())
            value = envelope.value
            if not isinstance(value, list):
                raise AssertionError(
                    f"loop {node.id!r} [*] output {external_name!r}: envelope "
                    f"value is {type(value).__name__}, expected list"
                )
            if len(value) != expected:
                raise AssertionError(
                    f"loop {node.id!r} [*] output {external_name!r}: list has "
                    f"{len(value)} element(s), expected {expected} (count loop "
                    "should produce one per iteration)"
                )


OUTCOMES: dict[str, OutcomeFn] = {
    "job_completed": assert_job_completed,
    "all_declared_outputs_produced": assert_all_declared_outputs_produced,
    "envelopes_well_formed": assert_envelopes_well_formed,
    "all_nodes_succeeded_or_skipped": assert_all_nodes_succeeded_or_skipped,
    "node_artefacts_present": assert_node_artefacts_present,
    "pr_envelopes_correspond_to_real_prs": assert_pr_envelopes_correspond_to_real_prs,
    "branches_in_remote": assert_branches_in_remote,
    "count_loop_aggregations_have_expected_size": (
        assert_count_loop_aggregations_have_expected_size
    ),
}
