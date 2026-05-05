"""Filesystem layout for Hammock v1.

Engine owns the layout per design-patch §1.7. Types and tests use these
helpers; nothing should construct paths by string concatenation.

Layout (under ``<root>``):

    jobs/<job_slug>/
        job.json                       JobConfig
        events.jsonl                   append-only event log
        variables/<var_name>.json      typed variable envelopes
        nodes/<node_id>/state.json     NodeRun
        nodes/<node_id>/runs/<n>/      per-attempt agent artefacts
            prompt.md
            stdout.log
            stderr.log
            result.json
"""

from __future__ import annotations

from pathlib import Path


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


def variable_envelope_path(job_slug: str, var_name: str, *, root: Path) -> Path:
    return variables_dir(job_slug, root=root) / f"{var_name}.json"


def nodes_dir(job_slug: str, *, root: Path) -> Path:
    return job_dir(job_slug, root=root) / "nodes"


def node_dir(job_slug: str, node_id: str, *, root: Path) -> Path:
    return nodes_dir(job_slug, root=root) / node_id


def node_state_path(job_slug: str, node_id: str, *, root: Path) -> Path:
    return node_dir(job_slug, node_id, root=root) / "state.json"


def node_runs_dir(job_slug: str, node_id: str, *, root: Path) -> Path:
    return node_dir(job_slug, node_id, root=root) / "runs"


def node_attempt_dir(
    job_slug: str, node_id: str, attempt: int, *, root: Path
) -> Path:
    return node_runs_dir(job_slug, node_id, root=root) / str(attempt)


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
# Loop variable paths (T4+) — indexed by iteration
# ---------------------------------------------------------------------------


def _safe_loop_id(loop_id: str) -> str:
    """Replace path-unsafe characters in a loop id."""
    return loop_id.replace("/", "_").replace(" ", "_")


def loop_variable_envelope_path(
    job_slug: str,
    loop_id: str,
    var_name: str,
    iteration: int,
    *,
    root: Path,
) -> Path:
    """On-disk path for a body-produced variable inside a loop.

    Layout (flat, operator-friendly per design-patch §1.3):
    ``<job_dir>/variables/loop_<loop-id>_<var>_<i>.json``"""
    return (
        variables_dir(job_slug, root=root)
        / f"loop_{_safe_loop_id(loop_id)}_{var_name}_{iteration}.json"
    )
