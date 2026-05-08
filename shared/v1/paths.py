"""Filesystem layout for Hammock v1.

Engine owns the layout per design-patch §1.7. Types and tests use these
helpers; nothing should construct paths by string concatenation.

Layout (under ``<root>``):

    jobs/<job_slug>/
        job.json                                JobConfig
        events.jsonl                            append-only event log
        variables/<var>__<iter_token>.json      typed variable envelopes
        nodes/<node_id>/<iter_token>/state.json NodeRun (per-iter)
        nodes/<node_id>/<iter_token>/runs/<n>/  per-attempt agent artefacts
            prompt.md
            chat.jsonl
            stderr.log
            output.json                         agent's raw value JSON
        pending/<node_id>__<iter_token>.json    HIL markers

The ``iter_token`` axis is universal: top-level executions use the
literal string ``"top"`` so every path obeys one rule. Loop-body
executions use ``i<i0>`` (single nesting) or ``i<i0>_<i1>_...``
(deeper nesting), one int per enclosing loop, outermost first.
"""

from __future__ import annotations

from pathlib import Path

_TOP_TOKEN = "top"


def iter_token(iter_path: tuple[int, ...]) -> str:
    """Stringify *iter_path* into a flat, sortable, ASCII-only token.

    - ``()``           → ``"top"``
    - ``(0,)``         → ``"i0"``
    - ``(0, 1)``       → ``"i0_1"``
    - ``(2, 0, 4)``    → ``"i2_0_4"``

    Negative indices are rejected — iter_path components are loop
    iteration indices, which are always >= 0.
    """
    for i in iter_path:
        if i < 0:
            raise ValueError(f"iter_path component must be >= 0, got {i!r} in {iter_path!r}")
    if not iter_path:
        return _TOP_TOKEN
    return "i" + "_".join(str(i) for i in iter_path)


def parse_iter_token(token: str) -> tuple[int, ...]:
    """Inverse of :func:`iter_token`. Round-trips both directions.

    Raises :class:`ValueError` on malformed tokens (no leading ``i``,
    non-digit components, empty body after ``i``).
    """
    if token == _TOP_TOKEN:
        return ()
    if not token.startswith("i"):
        raise ValueError(f"iter token must start with 'i' or be 'top', got {token!r}")
    body = token[1:]
    if not body:
        raise ValueError(f"iter token has empty body: {token!r}")
    parts = body.split("_")
    out: list[int] = []
    for p in parts:
        if not p.isdigit():
            raise ValueError(f"iter token component {p!r} is not a non-negative integer")
        out.append(int(p))
    return tuple(out)


def jobs_dir(*, root: Path) -> Path:
    return root / "jobs"


def job_dir(job_slug: str, *, root: Path) -> Path:
    return jobs_dir(root=root) / job_slug


def job_config_path(job_slug: str, *, root: Path) -> Path:
    return job_dir(job_slug, root=root) / "job.json"


def events_jsonl(job_slug: str, *, root: Path) -> Path:
    return job_dir(job_slug, root=root) / "events.jsonl"


def variables_dir(job_slug: str, *, root: Path) -> Path:
    return job_dir(job_slug, root=root) / "variables"


def variable_envelope_path(
    job_slug: str,
    var_name: str,
    iter_path: tuple[int, ...] = (),
    *,
    root: Path,
) -> Path:
    """Variable envelope path keyed by full iter_path.

    Top-level: ``<job_dir>/variables/<var>__top.json``
    Loop body: ``<job_dir>/variables/<var>__i<...>.json``
    """
    return variables_dir(job_slug, root=root) / f"{var_name}__{iter_token(iter_path)}.json"


def nodes_dir(job_slug: str, *, root: Path) -> Path:
    return job_dir(job_slug, root=root) / "nodes"


def node_dir(job_slug: str, node_id: str, *, root: Path) -> Path:
    """Container for every iteration of *node_id* under this job."""
    return nodes_dir(job_slug, root=root) / node_id


def node_iter_dir(
    job_slug: str,
    node_id: str,
    iter_path: tuple[int, ...] = (),
    *,
    root: Path,
) -> Path:
    """Per-(node, iter_path) container. Holds ``state.json`` and ``runs/``."""
    return node_dir(job_slug, node_id, root=root) / iter_token(iter_path)


def node_state_path(
    job_slug: str,
    node_id: str,
    iter_path: tuple[int, ...] = (),
    *,
    root: Path,
) -> Path:
    return node_iter_dir(job_slug, node_id, iter_path, root=root) / "state.json"


def node_runs_dir(
    job_slug: str,
    node_id: str,
    iter_path: tuple[int, ...] = (),
    *,
    root: Path,
) -> Path:
    return node_iter_dir(job_slug, node_id, iter_path, root=root) / "runs"


def node_attempt_dir(
    job_slug: str,
    node_id: str,
    attempt: int,
    iter_path: tuple[int, ...] = (),
    *,
    root: Path,
) -> Path:
    """Per-attempt directory under a (node, iter_path).

    Attempts are numbered per (node_id, iter_path) — each fresh iter
    starts at attempt 1.
    """
    return node_runs_dir(job_slug, node_id, iter_path, root=root) / str(attempt)


def pending_dir(job_slug: str, *, root: Path) -> Path:
    return job_dir(job_slug, root=root) / "pending"


def pending_marker_path(
    job_slug: str,
    node_id: str,
    iter_path: tuple[int, ...] = (),
    *,
    root: Path,
) -> Path:
    """HIL pending marker path keyed by (node_id, iter_path).

    Filename: ``<node_id>__<iter_token>.json``. Top-level HIL gates use
    the ``top`` token; loop body gates use ``i<...>``.
    """
    return pending_dir(job_slug, root=root) / f"{node_id}__{iter_token(iter_path)}.json"


def ensure_job_layout(job_slug: str, *, root: Path) -> Path:
    """Create the standard skeleton dirs for a freshly-submitted job.
    Idempotent. Returns the job_dir."""
    jd = job_dir(job_slug, root=root)
    jd.mkdir(parents=True, exist_ok=True)
    variables_dir(job_slug, root=root).mkdir(parents=True, exist_ok=True)
    nodes_dir(job_slug, root=root).mkdir(parents=True, exist_ok=True)
    return jd


# ---------------------------------------------------------------------------
# Code substrate paths (T3+)
# ---------------------------------------------------------------------------


def repo_clone_dir(job_slug: str, *, root: Path) -> Path:
    """Engine's local clone of the test repo. Created at submit-time when
    the workflow has any code-kind nodes; serves as the parent for stage
    worktrees."""
    return job_dir(job_slug, root=root) / "repo"


def worktrees_dir(job_slug: str, *, root: Path) -> Path:
    return job_dir(job_slug, root=root) / "worktrees"


def node_worktree_dir(job_slug: str, node_id: str, *, root: Path) -> Path:
    """Per-code-node worktree directory. The agent edits files here; the
    engine pushes commits from the parent clone."""
    return worktrees_dir(job_slug, root=root) / node_id


def job_branch_name(job_slug: str) -> str:
    return f"hammock/jobs/{job_slug}"


def stage_branch_name(job_slug: str, node_id: str) -> str:
    return f"hammock/stages/{job_slug}/{node_id}"


# ---------------------------------------------------------------------------
# Deprecated v1.0 helpers — retained as compat shims during the loops-v2
# migration. Callers are being moved to the iter_path-keyed helpers above
# step by step; once every caller threads ``iter_path``, these shims are
# deleted. Do not introduce new uses.
# ---------------------------------------------------------------------------


def _safe_loop_id(loop_id: str) -> str:
    """DEPRECATED. Replace path-unsafe characters in a legacy loop id."""
    return loop_id.replace("/", "_").replace(" ", "_")


def loop_variable_envelope_path(
    job_slug: str,
    loop_id: str,
    var_name: str,
    iteration: int,
    *,
    root: Path,
) -> Path:
    """DEPRECATED v1.0 indexed envelope path.

    Pre-loops-v2 layout: ``loop_<loop-id>_<var>_<i>.json``. Retained
    until every caller migrates to the iter_path-keyed
    :func:`variable_envelope_path`.
    """
    return (
        variables_dir(job_slug, root=root)
        / f"loop_{_safe_loop_id(loop_id)}_{var_name}_{iteration}.json"
    )
