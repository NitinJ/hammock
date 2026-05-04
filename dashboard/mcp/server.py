"""Per-stage MCP server — the four tools the agent calls.

Per design doc § HIL bridge § MCP tool surface. Three tools are
non-blocking; ``open_ask`` long-polls until the human answers or the item
is cancelled. All four are exposed over stdio via FastMCP and bound to a
fixed ``(job_slug, stage_id, root)`` tuple at process startup.

Tools:

- ``open_task``: writes ``stages/<sid>/tasks/<task_id>/task.json`` with
  ``state=RUNNING`` and returns ``{task_id}``.
- ``update_task``: mutates an existing ``task.json`` to the requested
  status. Accepts an optional ``result`` dict that is persisted as a
  sidecar ``task-result.json``.
- ``open_ask``: writes ``hil/<item_id>.json`` (status ``awaiting``);
  awaits a filesystem modification that flips status to ``answered`` or
  ``cancelled``; returns the ``HilAnswer`` dict (or raises on cancellation).
- ``append_stages``: appends ``StageDefinition`` objects to
  ``stage-list.yaml`` for expander stages.

The module is also runnable: ``python -m dashboard.mcp <job_slug>
<stage_id> [--root <path>]`` enters stdio mode and registers the four
tools against a FastMCP server named ``hammock-dashboard``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from shared.atomic import atomic_write_json, atomic_write_text
from shared.models.hil import (
    AskAnswer,
    AskQuestion,
    HilItem,
    ManualStepAnswer,
    ManualStepQuestion,
    ReviewAnswer,
    ReviewQuestion,
)
from shared.models.task import TaskRecord, TaskState
from shared.paths import (
    hil_item_path,
    job_stage_list,
    task_dir,
    task_json,
)

_SERVER_NAME = "hammock-dashboard"

# Caller-controlled identifiers (task_id, stage id) flow into filesystem paths
# via shared.paths helpers. A permissive regex prevents path-traversal
# (``../foo``) and shell-special characters from reaching ``Path``.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class MCPToolError(Exception):
    """Raised by tool implementations to surface a structured error to MCP.

    FastMCP wraps this into a JSON-RPC error response; CLI agents see a
    typed tool failure rather than an opaque crash.
    """


def _validate_slug(label: str, value: str) -> None:
    if not value or not _SLUG_RE.fullmatch(value):
        raise MCPToolError(f"invalid {label}: {value!r}")


# ---------------------------------------------------------------------------
# ID generators
# ---------------------------------------------------------------------------


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(tz=UTC)


def _make_task_id(stamp: datetime) -> str:
    return f"task_{stamp.strftime('%Y-%m-%dT%H:%M:%S')}_{secrets.token_hex(3)}"


def _make_hil_id(kind: str, stamp: datetime) -> str:
    # Thin shim around the shared helper so writers — open_ask here +
    # JobDriver._block_on_human via shared/hil_factory — produce the
    # same id shape (codex review on PR #28).
    from shared.hil_factory import make_hil_id

    return make_hil_id(kind, stamp)


# ---------------------------------------------------------------------------
# open_task
# ---------------------------------------------------------------------------


async def open_task(
    *,
    job_slug: str,
    stage_id: str,
    task_spec: str,
    worktree_branch: str,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Create a ``RUNNING`` task record and return its ``task_id``."""
    _validate_slug("job_slug", job_slug)
    _validate_slug("stage_id", stage_id)
    if not task_spec:
        raise MCPToolError("task_spec is required")
    if not worktree_branch:
        raise MCPToolError("worktree_branch is required")

    stamp = _now(now)
    task_id = _make_task_id(stamp)
    record = TaskRecord(
        task_id=task_id,
        stage_id=stage_id,
        state=TaskState.RUNNING,
        created_at=stamp,
        started_at=stamp,
        branch=worktree_branch,
    )
    atomic_write_json(task_json(job_slug, stage_id, task_id, root=root), record)
    spec_path = task_dir(job_slug, stage_id, task_id, root=root) / "task-spec.md"
    atomic_write_text(spec_path, task_spec if task_spec.endswith("\n") else task_spec + "\n")
    return {"task_id": task_id}


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------


async def update_task(
    *,
    job_slug: str,
    stage_id: str,
    task_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, bool]:
    """Update an existing task record. ``status`` must be a ``TaskState``.

    Concurrency: assumes single-writer-per-task. Concurrent ``update_task``
    calls to the same ``task_id`` are last-writer-wins on both ``task.json``
    and the ``task-result.json`` sidecar; agent-side dispatch is the
    serialisation point in v0.
    """
    _validate_slug("job_slug", job_slug)
    _validate_slug("stage_id", stage_id)
    _validate_slug("task_id", task_id)
    try:
        new_state = TaskState(status)
    except ValueError as exc:
        raise MCPToolError(f"invalid status {status!r}") from exc

    path = task_json(job_slug, stage_id, task_id, root=root)
    if not path.exists():
        raise MCPToolError(f"task {task_id!r} not found")

    try:
        record = TaskRecord.model_validate_json(path.read_text())
    except ValidationError as exc:
        raise MCPToolError(f"task {task_id!r} corrupted: {exc}") from exc

    stamp = _now(now)
    updated = record.model_copy(
        update={
            "state": new_state,
            "ended_at": stamp
            if new_state in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED)
            else record.ended_at,
        }
    )
    atomic_write_json(path, updated)

    if result is not None:
        result_path = task_dir(job_slug, stage_id, task_id, root=root) / "task-result.json"
        atomic_write_text(result_path, json.dumps(result, indent=2) + "\n")

    return {"ok": True}


# ---------------------------------------------------------------------------
# open_ask
# ---------------------------------------------------------------------------


def _build_question(
    kind: Literal["ask", "review", "manual-step"],
    fields: dict[str, Any],
) -> AskQuestion | ReviewQuestion | ManualStepQuestion:
    try:
        if kind == "ask":
            return AskQuestion(kind="ask", text=fields["text"], options=fields.get("options"))
        if kind == "review":
            return ReviewQuestion(kind="review", target=fields["target"], prompt=fields["prompt"])
        return ManualStepQuestion(
            kind="manual-step",
            instructions=fields["instructions"],
            extra_fields=fields.get("extra_fields"),
        )
    except (KeyError, ValidationError) as exc:
        raise MCPToolError(f"missing/invalid field for kind={kind!r}: {exc}") from exc


async def open_ask(
    *,
    job_slug: str,
    stage_id: str,
    kind: Literal["ask", "review", "manual-step"],
    task_id: str | None = None,
    root: Path | None = None,
    now: datetime | None = None,
    poll_interval: float = 0.1,
    timeout: float | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Long-poll: write a HIL item ``awaiting`` and block until answered.

    Returns the ``HilAnswer`` payload as a dict. Raises :class:`MCPToolError`
    if the item is cancelled before being answered (orphan-sweep scenario)
    or if ``timeout`` elapses with no answer.
    """
    _validate_slug("job_slug", job_slug)
    _validate_slug("stage_id", stage_id)
    if task_id is not None:
        _validate_slug("task_id", task_id)
    question = _build_question(kind, fields)
    stamp = _now(now)
    item_id = _make_hil_id(kind.replace("-", ""), stamp)
    item = HilItem(
        id=item_id,
        kind=kind,
        stage_id=stage_id,
        task_id=task_id,
        created_at=stamp,
        status="awaiting",
        question=question,
    )
    path = hil_item_path(job_slug, item_id, root=root)
    atomic_write_json(path, item)

    deadline = asyncio.get_event_loop().time() + timeout if timeout is not None else None
    while True:
        await asyncio.sleep(poll_interval)
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict):
            status = payload.get("status")
            if status == "answered":
                answer = payload.get("answer")
                if not isinstance(answer, dict):
                    raise MCPToolError(f"hil item {item_id!r} answered but answer field missing")
                return _validated_answer(kind, answer)
            if status == "cancelled":
                raise MCPToolError(f"hil item {item_id!r} was cancelled")

        if deadline is not None and asyncio.get_event_loop().time() >= deadline:
            raise MCPToolError(f"open_ask timeout waiting on {item_id!r}")


def _validated_answer(
    kind: Literal["ask", "review", "manual-step"], answer: dict[str, Any]
) -> dict[str, Any]:
    try:
        if kind == "ask":
            return AskAnswer.model_validate(answer).model_dump()
        if kind == "review":
            return ReviewAnswer.model_validate(answer).model_dump()
        return ManualStepAnswer.model_validate(answer).model_dump()
    except ValidationError as exc:
        raise MCPToolError(f"invalid answer payload: {exc}") from exc


# ---------------------------------------------------------------------------
# append_stages
# ---------------------------------------------------------------------------


def _check_validator_names_in_new_stages(stages: list[dict[str, Any]]) -> None:
    """Fail-closed check: reject any unknown validator names in the new stages."""
    from shared.artifact_validators import REGISTRY

    for spec in stages:
        ec = spec.get("exit_condition") or {}
        for ro in ec.get("required_outputs") or []:
            for name in ro.get("validators") or []:
                if name not in REGISTRY:
                    raise MCPToolError(
                        f"stage {spec.get('id')!r}: unknown validator {name!r}; "
                        f"registered names: {sorted(REGISTRY)}"
                    )
        for av in ec.get("artifact_validators") or []:
            schema = av.get("schema")
            if schema and schema not in REGISTRY:
                raise MCPToolError(
                    f"stage {spec.get('id')!r}: unknown artifact_validator schema {schema!r}; "
                    f"registered names: {sorted(REGISTRY)}"
                )


async def append_stages(
    *,
    job_slug: str,
    stages: list[dict[str, Any]],
    root: Path | None = None,
) -> dict[str, int | bool]:
    """Append ``StageDefinition`` objects to ``stage-list.yaml``."""
    _validate_slug("job_slug", job_slug)
    if not stages:
        raise MCPToolError("stages must be a non-empty list")

    path = job_stage_list(job_slug, root=root)
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as exc:
            raise MCPToolError(f"stage-list.yaml is malformed: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("stages"), list):
            raise MCPToolError("stage-list.yaml has no 'stages' list")
    else:
        data = {"stages": []}

    existing_ids = {s.get("id") for s in data["stages"] if isinstance(s, dict)}
    appended = 0
    for spec in stages:
        if "id" not in spec:
            raise MCPToolError(f"stage missing 'id': {spec!r}")
        _validate_slug("stage id", spec["id"])
        if spec["id"] in existing_ids:
            raise MCPToolError(f"duplicate stage id {spec['id']!r}")
        existing_ids.add(spec["id"])
        data["stages"].append(spec)
        appended += 1

    _check_validator_names_in_new_stages(stages)

    atomic_write_text(path, yaml.safe_dump(data, sort_keys=False))
    return {"ok": True, "count": appended}


# ---------------------------------------------------------------------------
# FastMCP wrapper
# ---------------------------------------------------------------------------


def build_server(
    *,
    job_slug: str,
    stage_id: str,
    root: Path | None = None,
) -> Any:
    """Construct a FastMCP server with the four tools bound to (job, stage)."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP(_SERVER_NAME)

    async def _open_task(task_spec: str, worktree_branch: str) -> dict[str, str]:
        try:
            return await open_task(
                job_slug=job_slug,
                stage_id=stage_id,
                task_spec=task_spec,
                worktree_branch=worktree_branch,
                root=root,
            )
        except MCPToolError as exc:
            raise ValueError(str(exc)) from exc

    async def _update_task(
        task_id: str,
        status: str,
        result: dict[str, Any] | None = None,
    ) -> dict[str, bool]:
        try:
            return await update_task(
                job_slug=job_slug,
                stage_id=stage_id,
                task_id=task_id,
                status=status,
                result=result,
                root=root,
            )
        except MCPToolError as exc:
            raise ValueError(str(exc)) from exc

    async def _open_ask(
        kind: Literal["ask", "review", "manual-step"],
        text: str | None = None,
        options: list[str] | None = None,
        target: str | None = None,
        prompt: str | None = None,
        instructions: str | None = None,
        extra_fields: dict[str, Any] | None = None,
        task_id: str | None = None,
        poll_interval: float = 0.1,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if text is not None:
            kwargs["text"] = text
        if options is not None:
            kwargs["options"] = options
        if target is not None:
            kwargs["target"] = target
        if prompt is not None:
            kwargs["prompt"] = prompt
        if instructions is not None:
            kwargs["instructions"] = instructions
        if extra_fields is not None:
            kwargs["extra_fields"] = extra_fields
        try:
            return await open_ask(
                job_slug=job_slug,
                stage_id=stage_id,
                kind=kind,
                task_id=task_id,
                root=root,
                poll_interval=poll_interval,
                timeout=timeout,
                **kwargs,
            )
        except MCPToolError as exc:
            raise ValueError(str(exc)) from exc

    async def _append_stages(stages: list[dict[str, Any]]) -> dict[str, int | bool]:
        try:
            return await append_stages(job_slug=job_slug, stages=stages, root=root)
        except MCPToolError as exc:
            raise ValueError(str(exc)) from exc

    server.add_tool(_open_task, name="open_task", description=open_task.__doc__ or "")
    server.add_tool(_update_task, name="update_task", description=update_task.__doc__ or "")
    server.add_tool(_open_ask, name="open_ask", description=open_ask.__doc__ or "")
    server.add_tool(_append_stages, name="append_stages", description=append_stages.__doc__ or "")
    return server


async def run_stdio(
    *,
    job_slug: str,
    stage_id: str,
    root: Path | None = None,
) -> None:
    """Run the per-stage server over stdio."""
    server = build_server(job_slug=job_slug, stage_id=stage_id, root=root)
    await server.run_stdio_async()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dashboard.mcp")
    parser.add_argument("job_slug")
    parser.add_argument("stage_id")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    asyncio.run(run_stdio(job_slug=args.job_slug, stage_id=args.stage_id, root=args.root))


__all__ = [
    "MCPToolError",
    "append_stages",
    "build_server",
    "main",
    "open_ask",
    "open_task",
    "run_stdio",
    "update_task",
]
