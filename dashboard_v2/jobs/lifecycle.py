"""Job lifecycle primitives: pause / resume / stop / delete.

The orchestrator subprocess polls ``control.md`` between iterations,
so pause/resume are pure file writes — the orchestrator picks up the
new state on its next checkpoint. Stop also writes ``state: cancelled``
into ``control.md`` (so the orchestrator can finalize cleanly) and, as
a belt-and-suspenders measure, sends SIGTERM to the orchestrator
subprocess group recorded in ``orchestrator.pid``. If SIGTERM doesn't
get the orchestrator to exit within the grace window, escalates to
SIGKILL.

Delete is hard: the job dir is removed in full. Only allowed when the
job is in a terminal state (``completed | failed | cancelled``).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import shutil
import signal
import time
from pathlib import Path

from dashboard_v2.api.projections import job_summary
from hammock_v2.engine import paths

log = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
PAUSABLE_STATES = frozenset({"running", "submitted"})


class LifecycleError(Exception):
    """Operator action against a job is invalid for the job's state."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat()


def _write_control(slug: str, state: str, root: Path | None = None) -> None:
    path = paths.control_md(slug, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nstate: {state}\nrequested_at: {_now()}\nrequested_by: operator\n---\n")


def pause_job(slug: str, *, root: Path | None = None) -> dict[str, str]:
    """Mark the job as paused. The orchestrator honors the request at its
    next checkpoint between Tasks."""
    summary = job_summary(slug, root=root if root is not None else paths.resolve_root())
    if summary is None:
        raise LifecycleError(f"job {slug!r} not found", status=404)
    state = summary.get("state", "submitted")
    if state in TERMINAL_STATES:
        raise LifecycleError(f"job is already {state}; cannot pause")
    _write_control(slug, "paused", root=root)
    return {"slug": slug, "controlled_state": "paused", "requested_at": _now()}


def resume_job(slug: str, *, root: Path | None = None) -> dict[str, str]:
    """Mark the job as running. Orchestrator's pause loop exits on next
    poll."""
    summary = job_summary(slug, root=root if root is not None else paths.resolve_root())
    if summary is None:
        raise LifecycleError(f"job {slug!r} not found", status=404)
    state = summary.get("state", "submitted")
    controlled_state = summary.get("controlled_state", "running")
    if state in TERMINAL_STATES:
        raise LifecycleError(f"job is already {state}; cannot resume")
    if controlled_state != "paused":
        raise LifecycleError(
            f"job is not paused (controlled_state={controlled_state!r}); cannot resume"
        )
    _write_control(slug, "running", root=root)
    return {"slug": slug, "controlled_state": "running", "requested_at": _now()}


def _finalize_job_md_cancelled(slug: str, root: Path | None = None) -> None:
    """Write `state: cancelled` to job.md if it is not already terminal.

    Used when the stop endpoint discovers the orchestrator subprocess
    is no longer alive — control.md alone won't move job.md (no
    orchestrator left to honor it), so we close the loop here.
    Idempotent: leaves completed/failed/cancelled job.md alone.
    """
    job_md_path = paths.job_md(slug, root=root)
    if not job_md_path.is_file():
        return
    text = job_md_path.read_text()
    # Cheap frontmatter probe — avoid pulling in projections (cycle).
    front: dict[str, str] = {}
    body_start = 0
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    front[k.strip()] = v.strip()
            body_start = end + len("\n---\n")
    current_state = front.get("state", "submitted")
    if current_state in TERMINAL_STATES:
        return
    front["state"] = "cancelled"
    front["finished_at"] = _now()
    front["error"] = "cancelled by operator"
    body = text[body_start:].lstrip("\n")
    lines = ["---"]
    for k in (
        "slug",
        "workflow",
        "state",
        "submitted_at",
        "started_at",
        "finished_at",
        "error",
    ):
        if front.get(k):
            lines.append(f"{k}: {front[k]}")
    lines.append("---")
    lines.append("")
    if body:
        lines.append(body.rstrip() + "\n")
    job_md_path.write_text("\n".join(lines))


def _signal_pgroup(pid: int, sig: signal.Signals) -> None:
    """Send a signal to the process group of *pid*. Best-effort —
    swallow ESRCH (process gone) and EPERM (someone else's pid)."""
    try:
        os.killpg(os.getpgid(pid), sig)
    except ProcessLookupError:
        return
    except PermissionError:
        log.warning("no permission to signal pgroup of pid=%s", pid)


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it; treat as alive.
        return True
    return True


def stop_job(
    slug: str,
    *,
    root: Path | None = None,
    grace_seconds: float = 30.0,
    sleep: float = 0.5,
) -> dict[str, str]:
    """Request cancellation. Writes control.md (orchestrator finalizes
    on next checkpoint) AND sends SIGTERM to the orchestrator subprocess
    group as a belt-and-suspenders measure. If still alive after
    ``grace_seconds``, escalates to SIGKILL."""
    summary = job_summary(slug, root=root if root is not None else paths.resolve_root())
    if summary is None:
        raise LifecycleError(f"job {slug!r} not found", status=404)
    state = summary.get("state", "submitted")
    if state in TERMINAL_STATES:
        raise LifecycleError(f"job is already {state}; cannot stop")
    _write_control(slug, "cancelled", root=root)

    pid_path = paths.orchestrator_pid_file(slug, root=root)
    if not pid_path.is_file():
        # No orchestrator subprocess on record. control.md says cancelled,
        # but nothing left to honor it — finalize job.md ourselves.
        _finalize_job_md_cancelled(slug, root=root)
        return {"slug": slug, "controlled_state": "cancelled", "killed": "no_pidfile"}

    try:
        pid = int(pid_path.read_text().strip())
    except (OSError, ValueError):
        _finalize_job_md_cancelled(slug, root=root)
        return {"slug": slug, "controlled_state": "cancelled", "killed": "bad_pidfile"}

    if not _is_alive(pid):
        # Orchestrator already gone (crash, shell kill, prior stop) — its
        # job.md never got the cancelled state written. Close the loop.
        _finalize_job_md_cancelled(slug, root=root)
        return {"slug": slug, "controlled_state": "cancelled", "killed": "already_dead"}

    _signal_pgroup(pid, signal.SIGTERM)

    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _is_alive(pid):
            return {"slug": slug, "controlled_state": "cancelled", "killed": "sigterm"}
        time.sleep(sleep)

    _signal_pgroup(pid, signal.SIGKILL)
    return {"slug": slug, "controlled_state": "cancelled", "killed": "sigkill"}


def delete_job(slug: str, *, root: Path | None = None) -> dict[str, str]:
    """Hard-delete a job dir. Only valid when the job is terminal."""
    summary = job_summary(slug, root=root if root is not None else paths.resolve_root())
    if summary is None:
        raise LifecycleError(f"job {slug!r} not found", status=404)
    state = summary.get("state", "submitted")
    if state not in TERMINAL_STATES:
        raise LifecycleError(
            f"job is {state}; only terminal jobs can be deleted (stop it first)",
            status=409,
        )
    jd = paths.job_dir(slug, root=root)
    if jd.is_dir():
        shutil.rmtree(jd)
    return {"slug": slug, "deleted": "true"}
