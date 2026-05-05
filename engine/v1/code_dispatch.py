"""Code-kind node dispatcher.

For a `code` + `agent` node:

1. Substrate has been allocated (worktree + stage branch off job branch).
2. Resolve inputs (from variable envelopes on disk).
3. Build the prompt — agent sees the worktree path + stage branch and is
   told to commit there. The `pr` variable type's render_for_producer
   tells the agent NOT to push or run gh pr create itself.
4. Spawn ``claude -p`` with cwd=worktree, ``--permission-mode bypassPermissions``.
5. Per declared output, run the type's `produce` with a code-aware
   NodeContext that exposes substrate fields + git/gh helpers.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from engine.v1 import git_ops
from engine.v1.prompt import OutputSlot, collect_output_slots
from engine.v1.resolver import resolve_node_inputs
from engine.v1.substrate import CodeSubstrate
from shared.atomic import atomic_write_text
from shared.v1 import paths
from shared.v1.envelope import make_envelope
from shared.v1.types.protocol import VariableTypeError
from shared.v1.types.registry import get_type
from shared.v1.workflow import CodeNode, Workflow

log = logging.getLogger(__name__)


@dataclass
class _CodeNodeContext:
    """NodeContext for `code` kind. Exposes substrate fields and git/gh
    helpers so variable types like `pr` can do post-actor mechanics.

    Loop-aware: when ``loop_id`` and ``iteration`` are set, ``expected_path``
    routes to the indexed envelope location."""

    var_name: str
    job_dir: Path
    actor_workdir: Path
    stage_branch: str
    base_branch: str
    repo_slug: str
    repo_dir: Path
    loop_id: str | None = None
    iteration: int | None = None

    def expected_path(self) -> Path:
        if self.loop_id is not None and self.iteration is not None:
            slug = self.job_dir.name
            root = self.job_dir.parent.parent
            return paths.loop_variable_envelope_path(
                slug, self.loop_id, self.var_name, self.iteration, root=root
            )
        return self.job_dir / "variables" / f"{self.var_name}.json"

    @property
    def repo(self) -> str:
        # `pr` type checks for `repo` attribute.
        return self.repo_slug

    # --- engine helpers exposed to types ---

    def branch_has_commits(self, branch: str, *, base: str) -> bool:
        return git_ops.has_commits_beyond(self.repo_dir, branch, base=base)

    def git_push(self, branch: str) -> None:
        # Hammock owns stage branches under `hammock/stages/*`; successive
        # runs (or substrate-shared loop iterations) may diverge from the
        # remote tip. Force-push (with lease) is safe for these.
        force = branch.startswith("hammock/stages/")
        git_ops.push_branch(self.repo_dir, branch, force=force)

    def gh_create_pr(
        self,
        *,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool = False,
    ) -> str:
        return git_ops.gh_create_pr(
            self.repo_dir,
            head=head,
            base=base,
            title=title,
            body=body,
            draft=draft,
        )

    def latest_commit_subject(self, branch: str) -> str:
        return git_ops.latest_commit_subject(self.repo_dir, branch)

    def latest_commit_body(self, branch: str) -> str:
        return git_ops.latest_commit_body(self.repo_dir, branch)


@dataclass
class _CodePromptCtx:
    """Prompt-building context for code kind."""

    var_name: str
    job_dir: Path
    actor_workdir: Path
    stage_branch: str
    base_branch: str

    def expected_path(self) -> Path:
        return self.job_dir / "variables" / f"{self.var_name}.json"


@dataclass
class CodeDispatchResult:
    succeeded: bool
    attempt_dir: Path
    error: str | None = None


ClaudeRunner = Callable[[str, Path, Path], subprocess.CompletedProcess[str]]
"""Spawns `claude -p <prompt>` with cwd=<worktree>; logs go to attempt_dir."""


def _default_claude_runner(
    prompt: str, attempt_dir: Path, worktree: Path
) -> subprocess.CompletedProcess[str]:
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
            cwd=str(worktree),
            stdout=out,
            stderr=err,
            check=False,
        )


def dispatch_code_agent(
    *,
    node: CodeNode,
    workflow: Workflow,
    job_slug: str,
    root: Path,
    substrate: CodeSubstrate,
    attempt: int = 1,
    claude_runner: ClaudeRunner | None = None,
    loop_id: str | None = None,
    iteration: int | None = None,
) -> CodeDispatchResult:
    """Run a single code + agent node end-to-end.

    Pre-condition: ``substrate`` was already allocated for this node by
    the driver (worktree exists, stage branch exists locally).
    Post-condition on success: every declared output has a typed
    envelope on disk and (for `pr` outputs) a real PR is open on GitHub.

    When invoked inside a loop body, ``loop_id`` and ``iteration`` route
    envelope writes to the indexed path layout."""
    runner = claude_runner or _default_claude_runner

    job_dir = paths.job_dir(job_slug, root=root)
    attempt_dir = paths.node_attempt_dir(job_slug, node.id, attempt, root=root)
    attempt_dir.mkdir(parents=True, exist_ok=True)

    # 1. Resolve inputs (loop-aware).
    resolved = resolve_node_inputs(
        node=node,
        workflow=workflow,
        job_slug=job_slug,
        root=root,
        loop_id=loop_id,
        iteration=iteration,
    )

    # 2. Build prompt with code-aware context (so the `pr` type's
    # render_for_producer can show the worktree path + branch names).
    prompt = _build_code_prompt(
        node=node,
        workflow=workflow,
        inputs=resolved,
        job_dir=job_dir,
        substrate=substrate,
    )
    atomic_write_text(attempt_dir / "prompt.md", prompt)

    # 3. Spawn agent in the worktree.
    log.info(
        "code-dispatch: %s/%s (attempt %d) cwd=%s branch=%s",
        job_slug,
        node.id,
        attempt,
        substrate.worktree,
        substrate.stage_branch,
    )
    completed = runner(prompt, attempt_dir, substrate.worktree)
    if completed.returncode != 0:
        return CodeDispatchResult(
            succeeded=False,
            attempt_dir=attempt_dir,
            error=(
                f"claude subprocess failed: rc={completed.returncode}. "
                f"See {attempt_dir / 'stderr.log'}."
            ),
        )

    # 4. Run produce for each declared output.
    output_slots = collect_output_slots(node, workflow)
    try:
        _produce_code_outputs(
            slots=output_slots,
            node_id=node.id,
            job_slug=job_slug,
            root=root,
            substrate=substrate,
            loop_id=loop_id,
            iteration=iteration,
        )
    except VariableTypeError as exc:
        return CodeDispatchResult(
            succeeded=False,
            attempt_dir=attempt_dir,
            error=f"output contract failed: {exc}",
        )
    except git_ops.GitError as exc:
        return CodeDispatchResult(
            succeeded=False,
            attempt_dir=attempt_dir,
            error=f"git error during produce: {exc}",
        )
    except git_ops.GhError as exc:
        return CodeDispatchResult(
            succeeded=False,
            attempt_dir=attempt_dir,
            error=f"gh error during produce: {exc}",
        )

    return CodeDispatchResult(succeeded=True, attempt_dir=attempt_dir)


def _build_code_prompt(
    *,
    node: CodeNode,
    workflow: Workflow,
    inputs: dict,
    job_dir: Path,
    substrate: CodeSubstrate,
) -> str:
    """Wrap the artifact prompt builder with code-aware substrate hints
    so per-output `render_for_producer` (for `pr`, `branch`, etc.) sees
    the worktree + branch names."""
    from engine.v1.prompt import _PromptCtx as _ArtifactPromptCtx

    # The artifact-prompt builder uses _PromptCtx for rendering each
    # output. For code outputs the type needs more context (worktree,
    # branch names). We monkey-patch the prompt builder's _PromptCtx
    # via a small wrapper — cleaner long-term: prompt.build_prompt
    # accepts a ctx_factory. For now we inline a custom build.
    parts: list[str] = []
    parts.append(f"# Node: {node.id}")
    parts.append("")
    parts.append(
        "You are an agent acting on a Hammock workflow. This is a "
        "**code** node — you have a working directory (a git worktree) "
        "and a branch checked out. Make code edits there, stage them, "
        "and commit on the current branch. The engine handles `git push` "
        "and `gh pr create` after your stage exits. Do not run those "
        "commands yourself."
    )
    parts.append("")
    parts.append("## Working directory")
    parts.append("")
    parts.append(f"`{substrate.worktree}`")
    parts.append("")
    parts.append("## Branch")
    parts.append("")
    parts.append(
        f"You are on `{substrate.stage_branch}`, forked from "
        f"`{substrate.base_branch}`. Commit your changes here."
    )
    parts.append("")

    # Inputs — render via each input's variable type.
    if inputs:
        parts.append("## Inputs")
        parts.append("")
        for slot_name, slot in inputs.items():
            if not slot.present:
                if slot.optional:
                    parts.append(f"### Input `{slot_name}` (optional, not produced)")
                    parts.append("")
                    parts.append("(no upstream value yet — proceed without it)")
                    parts.append("")
                continue
            from pydantic import BaseModel

            if isinstance(slot.value, BaseModel):
                # Look up the type by matching the value's class against
                # the workflow's variables.
                type_name = _type_name_from_value(slot.value, workflow)
                if type_name:
                    type_obj = get_type(type_name)
                    ctx = _ArtifactPromptCtx(var_name=slot_name, job_dir=job_dir)
                    parts.append(type_obj.render_for_consumer(type_obj.Decl(), slot.value, ctx))
                    parts.append("")
                    continue
            parts.append(f"### Input `{slot_name}`")
            parts.append("")
            parts.append(f"```\n{slot.value}\n```")
            parts.append("")

    # Outputs — render with code-aware ctx so `pr` etc see the substrate.
    output_slots = collect_output_slots(node, workflow)
    if output_slots:
        parts.append("## Outputs")
        parts.append("")
        for slot in output_slots:
            type_obj = get_type(slot.type_name)
            ctx = _CodePromptCtx(
                var_name=slot.var_name,
                job_dir=job_dir,
                actor_workdir=substrate.worktree,
                stage_branch=substrate.stage_branch,
                base_branch=substrate.base_branch,
            )
            parts.append(type_obj.render_for_producer(type_obj.Decl(), ctx))
            if slot.optional:
                parts.append(
                    f"_Output `{slot.slot_name}` is optional. Skip "
                    "producing it if the work was not needed._"
                )
            parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _type_name_from_value(value: object, workflow: Workflow) -> str | None:
    from pydantic import BaseModel

    if not isinstance(value, BaseModel):
        return None
    for spec in workflow.variables.values():
        try:
            t = get_type(spec.type)
        except Exception:
            continue
        if isinstance(value, t.Value):
            return spec.type
    return None


def _produce_code_outputs(
    *,
    slots: list[OutputSlot],
    node_id: str,
    job_slug: str,
    root: Path,
    substrate: CodeSubstrate,
    loop_id: str | None = None,
    iteration: int | None = None,
) -> None:
    job_dir = paths.job_dir(job_slug, root=root)
    paths.variables_dir(job_slug, root=root).mkdir(parents=True, exist_ok=True)

    for slot in slots:
        type_obj = get_type(slot.type_name)
        ctx = _CodeNodeContext(
            var_name=slot.var_name,
            job_dir=job_dir,
            actor_workdir=substrate.worktree,
            stage_branch=substrate.stage_branch,
            base_branch=substrate.base_branch,
            repo_slug=substrate.repo_slug,
            repo_dir=substrate.repo_dir,
            loop_id=loop_id,
            iteration=iteration,
        )
        if slot.optional:
            from shared.v1.types.protocol import VariableTypeError as _VTErr

            try:
                value = type_obj.produce(type_obj.Decl(), ctx)
            except _VTErr as exc:
                if "no commits beyond" in str(exc):
                    log.info(
                        "optional output %s skipped (no commits) for %s",
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
            repo=substrate.repo_slug,
        )
        if loop_id is not None and iteration is not None:
            target = paths.loop_variable_envelope_path(
                job_slug, loop_id, slot.var_name, iteration, root=root
            )
        else:
            target = paths.variable_envelope_path(job_slug, slot.var_name, root=root)
        atomic_write_text(target, env.model_dump_json())
