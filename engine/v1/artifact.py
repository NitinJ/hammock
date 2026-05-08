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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

    ``iter_path`` keys this execution: empty tuple = top-level, otherwise
    one int per enclosing loop (outermost first). Both
    ``expected_path`` (the envelope target) and ``attempt_output_path``
    (the agent's raw output.json) use the same iter token derived from
    this tuple."""

    var_name: str
    job_dir: Path
    iter_path: tuple[int, ...] = ()
    attempt_dir: Path | None = None
    inputs: dict[str, Any] = field(default_factory=dict)

    def expected_path(self) -> Path:
        slug = self.job_dir.name
        root = self.job_dir.parent.parent
        return paths.variable_envelope_path(slug, self.var_name, self.iter_path, root=root)

    def attempt_output_path(self) -> Path:
        """Per-attempt raw output.json the agent writes. Distinct from the
        envelope path so a missing output can't be confused with a stale
        envelope."""
        if self.attempt_dir is None:
            raise RuntimeError(
                "attempt_output_path requires attempt_dir on the context — "
                "the dispatcher must populate it before invoking produce"
            )
        return self.attempt_dir / "output.json"


@dataclass
class DispatchResult:
    succeeded: bool
    attempt_dir: Path
    error: str | None = None


# Type alias for the claude invocation; injectable for unit tests.
# Third arg is the working directory: the project repo clone when the
# job has a repo (Stage 3 — every agent node runs rooted in the repo so
# CLAUDE.md and project files are visible). ``None`` for artifact-only
# workflows that have no repo (T1 fixtures only — production paths
# always have one).
ClaudeRunner = Callable[[str, Path, Path | None], subprocess.CompletedProcess[str]]


def _default_claude_runner(
    prompt: str, attempt_dir: Path, cwd: Path | None
) -> subprocess.CompletedProcess[str]:
    """Default invocation of `claude -p`.

    We pass the prompt as a CLI argument (not stdin) and run with
    --permission-mode bypassPermissions so the agent can write files
    without prompting. Output is captured as JSONL via
    --output-format stream-json (one JSON object per line: system /
    assistant / user / result), so the dashboard can render the
    turn-by-turn transcript. ``--verbose`` is required by claude when
    pairing stream-json with ``-p``.

    When ``cwd`` is supplied, the subprocess runs there — this is the
    project repo clone for any job whose workflow has a repo, giving
    the agent free access to ``CLAUDE.md`` and project files.
    """
    chat_path = attempt_dir / "chat.jsonl"
    stderr_path = attempt_dir / "stderr.log"
    with chat_path.open("wb") as out, stderr_path.open("wb") as err:
        return subprocess.run(
            [
                "claude",
                "-p",
                prompt,
                "--permission-mode",
                "bypassPermissions",
                "--output-format",
                "stream-json",
                "--verbose",
            ],
            cwd=str(cwd) if cwd is not None else None,
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
    workflow_dir: Path | None = None,
    repo_dir: Path | None = None,
    iter_path: tuple[int, ...] = (),
) -> DispatchResult:
    """Run a single artifact + agent node end-to-end.

    ``iter_path`` keys this execution. Empty tuple = top-level node;
    otherwise one int per enclosing loop, outermost first.

    ``repo_dir`` is the project's repo clone at ``<job_dir>/repo``. When
    set, the spawned claude process runs with cwd there — Stage 3's
    working-directory rule, giving the agent free access to
    ``CLAUDE.md`` and project files. Artifact-only workflows (no
    code-kind nodes anywhere) have no repo and pass ``None``."""
    runner = claude_runner or _default_claude_runner

    job_dir = paths.job_dir(job_slug, root=root)
    attempt_dir = paths.node_attempt_dir(job_slug, node.id, attempt, iter_path, root=root)
    attempt_dir.mkdir(parents=True, exist_ok=True)

    # 1. Resolve inputs.
    resolved = resolve_node_inputs(
        node=node,
        workflow=workflow,
        job_slug=job_slug,
        root=root,
        iter_path=iter_path,
    )

    # 2. Build prompt.
    prompt = build_prompt(
        node=node,
        workflow=workflow,
        inputs=resolved,
        job_dir=job_dir,
        workflow_dir=workflow_dir,
        attempt_dir=attempt_dir,
        iter_path=iter_path,
    )
    atomic_write_text(attempt_dir / "prompt.md", prompt)

    # 3. Spawn agent — rooted in the project repo when one exists, so
    # CLAUDE.md + repo files are visible to Read/grep without an extra
    # configuration step.
    log.info("dispatching %s/%s (attempt %d) cwd=%s", job_slug, node.id, attempt, repo_dir)
    completed = runner(prompt, attempt_dir, repo_dir)
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
            attempt_dir=attempt_dir,
            iter_path=iter_path,
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
    attempt_dir: Path,
    iter_path: tuple[int, ...],
) -> None:
    """Call each output type's `produce`, write envelopes to disk.

    Optional outputs (`?` suffix) that the agent didn't produce are
    silently skipped. Required outputs that are absent raise
    `VariableTypeError` from the type's own check, which propagates up.

    The type's ``produce`` reads the agent's raw value-JSON from
    ``ctx.attempt_output_path()`` and validates it. The dispatcher (this
    function) is responsible for wrapping the validated value in an
    Envelope and writing it to the iter-keyed variable path.
    """
    job_dir = paths.job_dir(job_slug, root=root)
    paths.variables_dir(job_slug, root=root).mkdir(parents=True, exist_ok=True)

    for slot in slots:
        type_obj = get_type(slot.type_name)
        ctx = _NodeContext(
            var_name=slot.var_name,
            job_dir=job_dir,
            iter_path=iter_path,
            attempt_dir=attempt_dir,
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
        target = paths.variable_envelope_path(job_slug, slot.var_name, iter_path, root=root)
        atomic_write_text(target, env.model_dump_json())
