"""Artifact-kind node dispatcher.

For an `artifact` + `agent` node:
1. Resolve inputs.
2. Build the prompt.
3. Persist prompt + node attempt directory.
4. Spawn `claude -p <prompt>`, capture stdout/stderr, await exit.
5. For each declared output: run the type's `produce(decl, ctx)`,
   serialise to envelope, write to disk.
6. Return summary (stdout, stderr, attempt dir path).

For `artifact` + `human` (T2+) and `artifact` + `engine`: separate
dispatch paths added later — this module starts with the agent path.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from engine.v1.prompt import OutputSlot, build_prompt, collect_output_slots
from engine.v1.resolver import resolve_node_inputs
from shared.atomic import atomic_write_text
from shared.v1 import paths
from shared.v1.envelope import make_envelope
from shared.v1.types.protocol import VariableTypeError
from shared.v1.types.registry import get_type
from shared.v1.workflow import ArtifactNode, Workflow

log = logging.getLogger(__name__)


@dataclass
class _NodeContext:
    """Concrete NodeContext for `artifact` kind. Exposes only what the
    type's `produce` needs for an artifact node — no worktree, no branch,
    no repo helpers (those are `code` kind concerns).

    When the node runs inside a loop, ``loop_id`` and ``iteration`` are
    set; ``expected_path`` then resolves to the loop-indexed envelope
    location instead of the plain one."""

    var_name: str
    job_dir: Path
    loop_id: str | None = None
    iteration: int | None = None

    def expected_path(self) -> Path:
        if self.loop_id is not None and self.iteration is not None:
            from shared.v1 import paths as _paths

            # Carry the job_dir's parent layout into the loop helper.
            # job_dir = <root>/jobs/<slug>; we recompute via _paths so
            # the layout stays in one place.
            slug = self.job_dir.name
            root = self.job_dir.parent.parent
            return _paths.loop_variable_envelope_path(
                slug, self.loop_id, self.var_name, self.iteration, root=root
            )
        return self.job_dir / "variables" / f"{self.var_name}.json"


@dataclass
class DispatchResult:
    succeeded: bool
    attempt_dir: Path
    error: str | None = None


# Type alias for the claude invocation; injectable for unit tests.
ClaudeRunner = Callable[[str, Path], subprocess.CompletedProcess[str]]


def _default_claude_runner(prompt: str, attempt_dir: Path) -> subprocess.CompletedProcess[str]:
    """Default invocation of `claude -p`.

    We pass the prompt as a CLI argument (not stdin) and run with
    --permission-mode bypassPermissions so the agent can write files
    without prompting. Output streams are redirected to attempt_dir/
    stdout.log and stderr.log.
    """
    stdout_path = attempt_dir / "stdout.log"
    stderr_path = attempt_dir / "stderr.log"
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        return subprocess.run(
            [
                "claude",
                "-p",
                prompt,
                "--permission-mode",
                "bypassPermissions",
            ],
            stdout=out,
            stderr=err,
            check=False,
        )


def dispatch_artifact_agent(
    *,
    node: ArtifactNode,
    workflow: Workflow,
    job_slug: str,
    root: Path,
    attempt: int = 1,
    claude_runner: ClaudeRunner | None = None,
    loop_id: str | None = None,
    iteration: int | None = None,
) -> DispatchResult:
    """Run a single artifact + agent node end-to-end.

    When the node runs inside a loop, pass ``loop_id`` and ``iteration``
    so per-output produce + prompt rendering use the loop-indexed
    envelope paths."""
    runner = claude_runner or _default_claude_runner

    job_dir = paths.job_dir(job_slug, root=root)
    attempt_dir = paths.node_attempt_dir(job_slug, node.id, attempt, root=root)
    attempt_dir.mkdir(parents=True, exist_ok=True)

    # 1. Resolve inputs.
    resolved = resolve_node_inputs(
        node=node, workflow=workflow, job_slug=job_slug, root=root,
        loop_id=loop_id, iteration=iteration,
    )

    # 2. Build prompt.
    prompt = build_prompt(
        node=node, workflow=workflow, inputs=resolved, job_dir=job_dir,
        loop_id=loop_id, iteration=iteration,
    )
    atomic_write_text(attempt_dir / "prompt.md", prompt)

    # 3. Spawn agent.
    log.info("dispatching %s/%s (attempt %d)", job_slug, node.id, attempt)
    completed = runner(prompt, attempt_dir)
    if completed.returncode != 0:
        return DispatchResult(
            succeeded=False,
            attempt_dir=attempt_dir,
            error=(
                f"claude subprocess failed: rc={completed.returncode}. "
                f"See {attempt_dir / 'stderr.log'} for details."
            ),
        )

    # 4. Run produce for every declared output.
    output_slots = collect_output_slots(node, workflow)
    try:
        _produce_outputs(
            slots=output_slots,
            node_id=node.id,
            job_slug=job_slug,
            root=root,
            loop_id=loop_id,
            iteration=iteration,
        )
    except VariableTypeError as exc:
        return DispatchResult(
            succeeded=False,
            attempt_dir=attempt_dir,
            error=f"output contract failed: {exc}",
        )

    return DispatchResult(succeeded=True, attempt_dir=attempt_dir)


def _produce_outputs(
    *,
    slots: list[OutputSlot],
    node_id: str,
    job_slug: str,
    root: Path,
    loop_id: str | None = None,
    iteration: int | None = None,
) -> None:
    """Call each output type's `produce`, write envelopes to disk.

    Optional outputs (`?` suffix) that the agent didn't produce are
    silently skipped. Required outputs that are absent raise
    `VariableTypeError` from the type's own check, which propagates up.

    When invoked inside a loop body, ``loop_id`` + ``iteration`` route
    envelope writes to the indexed path layout (``loop_<id>_<var>_<i>.json``).
    """
    job_dir = paths.job_dir(job_slug, root=root)
    paths.variables_dir(job_slug, root=root).mkdir(parents=True, exist_ok=True)

    for slot in slots:
        type_obj = get_type(slot.type_name)
        ctx = _NodeContext(
            var_name=slot.var_name,
            job_dir=job_dir,
            loop_id=loop_id,
            iteration=iteration,
        )
        # Optional output: attempt produce but tolerate "not produced".
        if slot.optional:
            from shared.v1.types.protocol import VariableTypeError as _VTErr

            try:
                value = type_obj.produce(type_obj.Decl(), ctx)
            except _VTErr as exc:
                if "not produced" in str(exc) or "missing" in str(exc).lower():
                    log.info(
                        "optional output %s not produced by %s — skipping",
                        slot.var_name,
                        node_id,
                    )
                    continue
                raise
        else:
            value = type_obj.produce(type_obj.Decl(), ctx)

        env = make_envelope(
            type_name=slot.type_name,
            producer_node=node_id,
            value_payload=value.model_dump(mode="json"),
        )
        if loop_id is not None and iteration is not None:
            target = paths.loop_variable_envelope_path(
                job_slug, loop_id, slot.var_name, iteration, root=root
            )
        else:
            target = paths.variable_envelope_path(
                job_slug, slot.var_name, root=root
            )
        atomic_write_text(target, env.model_dump_json())
