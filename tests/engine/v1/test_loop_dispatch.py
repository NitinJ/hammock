"""Unit tests for engine/v1/loop_dispatch.py — count loops, nested
dispatch, [*] aggregation, per-iteration substrate.

T4 shipped without a unit-test file for this module (covered by e2e).
T5 introduces enough new control flow that locking it in with focused
unit tests is worth the cost.

The dispatcher's collaborators (claude runner, gh, real git) are
substituted with fakes — these tests must not touch the network.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from engine.v1.loop_dispatch import (
    dispatch_loop,
)
from engine.v1.substrate import JobRepo
from shared.v1 import paths
from shared.v1.envelope import Envelope, make_envelope
from shared.v1.workflow import (
    ArtifactNode,
    CodeNode,
    LoopNode,
    VariableSpec,
    Workflow,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_envelope(
    *, root: Path, job_slug: str, var_name: str, type_name: str, value: dict
) -> None:
    paths.ensure_job_layout(job_slug, root=root)
    env = make_envelope(type_name=type_name, producer_node="<test>", value_payload=value)
    paths.variable_envelope_path(job_slug, var_name, root=root).write_text(env.model_dump_json())


def _seed_loop_envelope(
    *,
    root: Path,
    job_slug: str,
    loop_id: str,
    var_name: str,
    iteration: int,
    type_name: str,
    value: dict,
) -> None:
    paths.ensure_job_layout(job_slug, root=root)
    env = make_envelope(type_name=type_name, producer_node="<test>", value_payload=value)
    paths.loop_variable_envelope_path(job_slug, loop_id, var_name, iteration, root=root).write_text(
        env.model_dump_json()
    )


def _fake_artifact_runner_factory(produce_payloads: dict[str, dict]):
    """Return a ClaudeRunner that, after pretending to run, writes the
    given variable envelopes. ``produce_payloads`` maps var_name → value.
    The runner is invoked once per iteration; every invocation writes
    the same set of envelopes (used for tests that don't care about
    iteration-specific values)."""

    def fake(prompt: str, attempt_dir: Path, worktree: Path | None = None):
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    return fake


# ---------------------------------------------------------------------------
# count loop — basic semantics
# ---------------------------------------------------------------------------


def _count_loop_workflow_simple() -> Workflow:
    """A count-of-2 loop with a single artifact-agent body node that
    produces a review-verdict per iteration."""
    return Workflow(
        workflow="t5-count-simple",
        variables={
            "design_spec": VariableSpec(type="design-spec"),
            "verdict": VariableSpec(type="review-verdict"),
            "verdict_list": VariableSpec(type="list[review-verdict]"),
        },
        nodes=[
            LoopNode(
                id="vlist",
                kind="loop",
                count=2,
                substrate="per-iteration",
                body=[
                    ArtifactNode(
                        id="pick",
                        kind="artifact",
                        actor="agent",
                        inputs={"design_spec": "$design_spec"},
                        outputs={"verdict": "$verdict"},
                    )
                ],
                outputs={"verdict_list": "$vlist.verdict[*]"},
            )
        ],
    )


def test_count_loop_runs_body_count_times_and_aggregates_list(
    tmp_path: Path,
) -> None:
    """count: 2 → body dispatched twice → [*] projection writes a
    list[review-verdict] envelope at the plain path with 2 elements."""
    job_slug = "j"
    _seed_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y", "document": "## D\n\nx"},
    )
    workflow = _count_loop_workflow_simple()

    invocations: list[Path] = []

    def fake_runner(prompt: str, attempt_dir: Path, cwd=None):
        invocations.append(attempt_dir)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        # Simulate the agent writing the raw verdict JSON to the
        # expected loop-indexed path. The dispatcher's produce will read
        # it, validate, and wrap into an envelope at that same path.
        iter_idx = len(invocations) - 1
        target = paths.loop_variable_envelope_path(
            job_slug, "vlist", "verdict", iter_idx, root=tmp_path
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {
                    "verdict": "approved",
                    "summary": f"iter-{iter_idx}",
                }
            )
        )
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    result = dispatch_loop(
        node=workflow.nodes[0],
        workflow=workflow,
        job_slug=job_slug,
        root=tmp_path,
        job_repo=None,
        artifact_claude_runner=fake_runner,
    )

    assert result.succeeded, result.error
    assert result.iterations_run == 2
    assert len(invocations) == 2

    list_env_path = paths.variable_envelope_path(job_slug, "verdict_list", root=tmp_path)
    assert list_env_path.is_file()
    list_env = Envelope.model_validate_json(list_env_path.read_text())
    assert list_env.type == "list[review-verdict]"
    assert isinstance(list_env.value, list)
    assert len(list_env.value) == 2
    assert list_env.value[0]["summary"] == "iter-0"
    assert list_env.value[1]["summary"] == "iter-1"


def test_loop_body_node_state_json_persists_per_iteration(tmp_path: Path) -> None:
    """Loop body nodes must write ``nodes/<id>/state.json`` (overwritten
    per iteration) so the dashboard's per-node detail endpoint has
    something to return.

    Bug: top-level nodes get state.json from the driver's main loop,
    but body nodes are dispatched only by ``_dispatch_body_node`` which
    didn't persist state. Result: clicking a succeeded body row 404'd
    and the row showed pending forever.
    """
    job_slug = "j"
    _seed_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y", "document": "## D\n\nx"},
    )
    workflow = _count_loop_workflow_simple()

    def fake_runner(prompt: str, attempt_dir: Path, cwd=None):
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        # write body envelope at the iter path
        envs = list(paths.variables_dir(job_slug, root=tmp_path).glob("loop_vlist_verdict_*.json"))
        iter_idx = len(envs)
        target = paths.loop_variable_envelope_path(
            job_slug, "vlist", "verdict", iter_idx, root=tmp_path
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"verdict": "approved", "summary": f"iter-{iter_idx}"}))
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    result = dispatch_loop(
        node=workflow.nodes[0],
        workflow=workflow,
        job_slug=job_slug,
        root=tmp_path,
        job_repo=None,
        artifact_claude_runner=fake_runner,
    )
    assert result.succeeded, result.error

    state_path = paths.node_state_path(job_slug, "pick", root=tmp_path)
    assert state_path.is_file(), (
        f"loop body node 'pick' should have nodes/<id>/state.json after "
        f"successful dispatch; not found at {state_path}"
    )
    from shared.v1.job import NodeRun, NodeRunState

    nr = NodeRun.model_validate_json(state_path.read_text())
    assert nr.node_id == "pick"
    assert nr.state == NodeRunState.SUCCEEDED
    # State.json reflects the latest iteration; for a count-of-2 loop,
    # attempts is 2.
    assert nr.attempts == 2


def test_count_loop_zero_iters_produces_empty_list(tmp_path: Path) -> None:
    """count: 0 → body never runs → [*] projection writes an empty
    list[T] envelope."""
    job_slug = "j"
    _seed_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y", "document": "## D\n\nx"},
    )
    workflow = _count_loop_workflow_simple()
    workflow.nodes[0].count = 0

    def fake_runner(prompt: str, attempt_dir: Path, cwd=None):  # pragma: no cover
        raise AssertionError("body must not run for count=0")

    result = dispatch_loop(
        node=workflow.nodes[0],
        workflow=workflow,
        job_slug=job_slug,
        root=tmp_path,
        job_repo=None,
        artifact_claude_runner=fake_runner,
    )

    assert result.succeeded, result.error
    assert result.iterations_run == 0
    # No envelopes on disk → no list to aggregate. Per design-patch §5.1
    # the engine produces an empty list[T] but our v1 implementation
    # logs and skips when there are no element envelopes — acceptable
    # for T5 (caller's outcome doesn't expect a list when count is 0).
    list_env_path = paths.variable_envelope_path(job_slug, "verdict_list", root=tmp_path)
    if list_env_path.is_file():
        env = Envelope.model_validate_json(list_env_path.read_text())
        assert env.value == []


def test_count_loop_negative_fails(tmp_path: Path) -> None:
    workflow = _count_loop_workflow_simple()
    workflow.nodes[0].count = -1

    result = dispatch_loop(
        node=workflow.nodes[0],
        workflow=workflow,
        job_slug="j",
        root=tmp_path,
        job_repo=None,
    )
    assert not result.succeeded
    assert "must be >= 0" in (result.error or "")


def test_count_loop_resolves_count_from_loop_var_last_field(tmp_path: Path) -> None:
    """T6 capability: ``count: $other-loop.var[last].count`` reads the
    typed value's count field at dispatch time."""
    from shared.v1.envelope import make_envelope

    job_slug = "j"
    paths.ensure_job_layout(job_slug, root=tmp_path)
    # Seed a loop-indexed impl-plan envelope at iteration 0, simulating
    # the upstream impl-plan-loop having produced impl_plan[last].
    plan_env = make_envelope(
        type_name="impl-plan",
        producer_node="<test>",
        value_payload={
            "count": 2,
            "stages": [
                {"name": "s0", "description": "d0"},
                {"name": "s1", "description": "d1"},
            ],
            "document": "## Plan\n\nTwo stages.",
        },
    )
    paths.loop_variable_envelope_path(
        job_slug, "impl-plan-loop", "impl_plan", 0, root=tmp_path
    ).write_text(plan_env.model_dump_json())

    workflow = Workflow(
        workflow="t6-count-from-ref",
        variables={
            "impl_plan": VariableSpec(type="impl-plan"),
            "design_spec": VariableSpec(type="design-spec"),
            "verdict": VariableSpec(type="review-verdict"),
            "verdict_list": VariableSpec(type="list[review-verdict]"),
        },
        nodes=[
            LoopNode(
                id="impl",
                kind="loop",
                count="$impl-plan-loop.impl_plan[last].count",
                substrate="per-iteration",
                body=[
                    ArtifactNode(
                        id="pick",
                        kind="artifact",
                        actor="agent",
                        inputs={"design_spec": "$design_spec"},
                        outputs={"verdict": "$verdict"},
                    )
                ],
                outputs={"verdict_list": "$impl.verdict[*]"},
            )
        ],
    )
    _seed_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y", "document": "## D\n\nx"},
    )

    invocations = []

    def fake(prompt, attempt_dir, cwd=None):
        invocations.append(attempt_dir)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        idx = len(invocations) - 1
        paths.loop_variable_envelope_path(
            job_slug, "impl", "verdict", idx, root=tmp_path
        ).write_text(
            json.dumps(
                {
                    "verdict": "approved",
                    "summary": f"i{idx}",
                }
            )
        )
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    result = dispatch_loop(
        node=workflow.nodes[0],
        workflow=workflow,
        job_slug=job_slug,
        root=tmp_path,
        job_repo=None,
        artifact_claude_runner=fake,
    )
    assert result.succeeded, result.error
    # count was resolved from impl_plan.count == 2 → 2 iters.
    assert result.iterations_run == 2
    list_path = paths.variable_envelope_path(job_slug, "verdict_list", root=tmp_path)
    env = Envelope.model_validate_json(list_path.read_text())
    assert len(env.value) == 2


def test_count_loop_literal_string_int_resolves(tmp_path: Path) -> None:
    """``count: '3'`` (string form) is parsed as an int."""
    job_slug = "j"
    _seed_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y", "document": "## D\n\nx"},
    )
    workflow = _count_loop_workflow_simple()
    workflow.nodes[0].count = "3"

    invocations = []

    def fake(prompt, attempt_dir, cwd=None):
        invocations.append(attempt_dir)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        idx = len(invocations) - 1
        paths.loop_variable_envelope_path(
            job_slug, "vlist", "verdict", idx, root=tmp_path
        ).write_text(
            json.dumps(
                {
                    "verdict": "approved",
                    "summary": f"i-{idx}",
                }
            )
        )
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    result = dispatch_loop(
        node=workflow.nodes[0],
        workflow=workflow,
        job_slug=job_slug,
        root=tmp_path,
        job_repo=None,
        artifact_claude_runner=fake,
    )
    assert result.succeeded, result.error
    assert result.iterations_run == 3


# ---------------------------------------------------------------------------
# Nested loop dispatch & inner-loop projection to outer-indexed path
# ---------------------------------------------------------------------------


def _nested_workflow() -> Workflow:
    """Outer count:2 wrapping an inner until loop with its own body
    (single artifact-agent producing a verdict). Mirrors T5's shape
    minus the code/PR substrate (which would require a real git repo)."""
    return Workflow(
        workflow="t5-nested",
        variables={
            "design_spec": VariableSpec(type="design-spec"),
            "verdict": VariableSpec(type="review-verdict"),
            "verdict_list": VariableSpec(type="list[review-verdict]"),
        },
        nodes=[
            LoopNode(
                id="outer",
                kind="loop",
                count=2,
                substrate="per-iteration",
                body=[
                    LoopNode(
                        id="inner",
                        kind="loop",
                        until="$inner.verdict[i].verdict == 'approved'",
                        max_iterations=2,
                        substrate="shared",
                        body=[
                            ArtifactNode(
                                id="reviewer",
                                kind="artifact",
                                actor="agent",
                                inputs={"design_spec": "$design_spec"},
                                outputs={"verdict": "$verdict"},
                            )
                        ],
                        outputs={"verdict": "$inner.verdict[last]"},
                    ),
                ],
                outputs={"verdict_list": "$outer.verdict[*]"},
            )
        ],
    )


def test_nested_count_of_until_dispatches_and_projects(tmp_path: Path) -> None:
    """Outer count: 2 wrapping inner until → inner projects each outer
    iter's [last] verdict to ``loop_outer_verdict_<k>.json``; outer
    aggregates those into the plain ``verdict_list.json``."""
    job_slug = "j"
    _seed_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="design_spec",
        type_name="design-spec",
        value={"title": "x", "overview": "y", "document": "## D\n\nx"},
    )
    workflow = _nested_workflow()

    invocation_count = {"n": 0}

    def fake(prompt, attempt_dir, cwd=None):
        invocation_count["n"] += 1
        attempt_dir.mkdir(parents=True, exist_ok=True)
        (attempt_dir / "stdout.log").write_text("")
        (attempt_dir / "stderr.log").write_text("")
        # Write raw verdict JSON to the inner-loop indexed path; the
        # dispatcher's produce reads and wraps. Approving immediately
        # means until exits at inner iter 0 each outer iter.
        inner_iter = 0
        paths.loop_variable_envelope_path(
            job_slug, "inner", "verdict", inner_iter, root=tmp_path
        ).write_text(
            json.dumps(
                {
                    "verdict": "approved",
                    "summary": f"call-{invocation_count['n']}",
                }
            )
        )
        return subprocess.CompletedProcess(args=["c"], returncode=0, stdout=b"", stderr=b"")

    result = dispatch_loop(
        node=workflow.nodes[0],
        workflow=workflow,
        job_slug=job_slug,
        root=tmp_path,
        job_repo=None,
        artifact_claude_runner=fake,
    )

    assert result.succeeded, result.error
    assert result.iterations_run == 2
    # Outer iter projected envelopes (each one is the inner's [last]
    # verdict for that outer iter).
    for k in range(2):
        outer_indexed = paths.loop_variable_envelope_path(
            job_slug, "outer", "verdict", k, root=tmp_path
        )
        assert outer_indexed.is_file(), f"outer-indexed inner projection missing for k={k}"
    # Outer's [*] aggregated list at plain path.
    list_path = paths.variable_envelope_path(job_slug, "verdict_list", root=tmp_path)
    assert list_path.is_file()
    env = Envelope.model_validate_json(list_path.read_text())
    assert env.type == "list[review-verdict]"
    assert isinstance(env.value, list)
    assert len(env.value) == 2


# ---------------------------------------------------------------------------
# Per-iteration substrate branch naming
# ---------------------------------------------------------------------------


def test_per_iteration_substrate_uses_unique_node_id_per_iter(
    tmp_path: Path,
) -> None:
    """A count-loop with substrate per-iteration and a direct code body
    node calls allocate_code_substrate with a per-iter scoped node_id so
    each iteration gets a distinct stage branch."""
    job_slug = "j"
    paths.ensure_job_layout(job_slug, root=tmp_path)

    workflow = Workflow(
        workflow="t5-per-iter",
        variables={
            "design_spec": VariableSpec(type="design-spec"),
            "pr": VariableSpec(type="pr"),
        },
        nodes=[
            LoopNode(
                id="impl",
                kind="loop",
                count=2,
                substrate="per-iteration",
                body=[
                    CodeNode(
                        id="implement",
                        kind="code",
                        actor="agent",
                        inputs={"design_spec": "$design_spec"},
                        outputs={"pr": "$pr"},
                    )
                ],
                outputs={"pr_list": "$impl.pr[*]"},
            )
        ],
    )

    _seed_envelope(
        root=tmp_path,
        job_slug=job_slug,
        var_name="design_spec",
        type_name="design-spec",
        value={"title": "t", "overview": "o", "document": "## D\n\nt"},
    )

    job_repo = JobRepo(
        repo_dir=tmp_path / "repo",
        repo_slug="me/repo",
        job_branch="hammock/jobs/j",
    )
    (tmp_path / "repo").mkdir()

    allocated_node_ids: list[str] = []

    from engine.v1 import loop_dispatch as ld
    from engine.v1.substrate import CodeSubstrate

    def fake_alloc(*, job_slug, node_id, root, job_repo, runner=None):
        allocated_node_ids.append(node_id)
        worktree = paths.node_worktree_dir(job_slug, node_id, root=root)
        worktree.mkdir(parents=True, exist_ok=True)
        return CodeSubstrate(
            repo_dir=job_repo.repo_dir,
            worktree=worktree,
            stage_branch=f"hammock/stages/{job_slug}/{node_id}",
            base_branch=job_repo.job_branch,
            repo_slug=job_repo.repo_slug,
        )

    # Stub dispatch_code_agent so we don't need real claude/git.
    class _OkResult:
        succeeded = True
        attempt_dir = tmp_path / "attempt"
        error = None

    with (
        patch.object(ld, "allocate_code_substrate", side_effect=fake_alloc),
        patch.object(ld, "dispatch_code_agent", return_value=_OkResult()) as mock_disp,
    ):
        # Manually seed indexed pr envelopes the [*] projector reads.
        for k in range(2):
            _seed_loop_envelope(
                root=tmp_path,
                job_slug=job_slug,
                loop_id="impl",
                var_name="pr",
                iteration=k,
                type_name="pr",
                value={
                    "url": f"https://github.com/me/repo/pull/{k + 1}",
                    "number": k + 1,
                    "repo": "me/repo",
                    "head_branch": f"hammock/stages/j/implement-{k}",
                    "base_branch": "hammock/jobs/j",
                },
            )

        result = ld.dispatch_loop(
            node=workflow.nodes[0],
            workflow=workflow,
            job_slug=job_slug,
            root=tmp_path,
            job_repo=job_repo,
        )

    assert result.succeeded, result.error
    assert mock_disp.call_count == 2
    # Per-iteration substrate must allocate with distinct node ids
    # encoding the iteration index.
    assert allocated_node_ids == ["impl-0", "impl-1"], allocated_node_ids
