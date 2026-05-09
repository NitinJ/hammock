"""Spawn a Claude orchestrator agent that walks a workflow.

The whole engine in v2 is: render an orchestrator prompt with the job's
context substituted in, spawn `claude -p` against it, and tail its
stream-json output to disk. Everything else — task dispatch, dependency
ordering, retries, human-in-the-loop polling — lives in the
orchestrator's prompt.
"""

from __future__ import annotations

import datetime as _dt
import logging
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from hammock_v2.engine import paths
from hammock_v2.engine.workflow import Workflow, load_workflow

log = logging.getLogger(__name__)

# Default location of the orchestrator + node prompt templates.
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"

# Type alias so tests can inject a fake claude.
ClaudeRunner = Callable[[list[str], Path, Path, Path], subprocess.CompletedProcess[bytes]]


@dataclass
class JobConfig:
    slug: str
    workflow_name: str
    request_text: str
    project_repo_path: Path | None = None


def render_orchestrator_prompt(
    *,
    job_dir: Path,
    workflow_path: Path,
    request_text: str,
    prompts_dir: Path = PROMPTS_DIR,
) -> str:
    """Read the orchestrator template and substitute job context.

    Substitutes $JOB_DIR, $WORKFLOW_PATH, $REQUEST_TEXT, $PROMPTS_DIR,
    and $HELPERS_DIR into the orchestrator template. $HELPERS_DIR
    resolves to `<prompts_dir>/helpers/`, the directory containing
    helper Task templates (prepare-node-input, process-expansion, etc.).
    """
    template_path = prompts_dir / "orchestrator.md"
    template = template_path.read_text()
    helpers_dir = prompts_dir / "helpers"
    if not helpers_dir.is_dir():
        log.warning(
            "v2 runner: helpers directory not found at %s; orchestrator "
            "helper Task spawning will fail",
            helpers_dir,
        )
    substitutions = {
        # Order matters: $HELPERS_DIR before $PROMPTS_DIR so the longer
        # variable name doesn't get partially substituted.
        "$HELPERS_DIR": str(helpers_dir),
        "$JOB_DIR": str(job_dir),
        "$WORKFLOW_PATH": str(workflow_path),
        "$REQUEST_TEXT": request_text,
        "$PROMPTS_DIR": str(prompts_dir),
    }
    rendered = template
    for key, value in substitutions.items():
        rendered = rendered.replace(key, value)
    return rendered


def write_job_md(job_dir: Path, payload: dict[str, str]) -> None:
    """Persist the top-level job state file as a small YAML/markdown blob."""
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
        if payload.get(k):
            lines.append(f"{k}: {payload[k]}")
    lines.append("---")
    lines.append("")
    lines.append("## Request")
    lines.append("")
    lines.append(payload.get("request", "").strip())
    lines.append("")
    (job_dir / "job.md").write_text("\n".join(lines))


def _default_runner(
    cmd: list[str], cwd: Path, stdout_path: Path, stderr_path: Path
) -> subprocess.CompletedProcess[bytes]:
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=out,
            stderr=err,
            check=False,
        )


def resolve_workflow_path(workflow_name: str, root: Path | None = None) -> Path:
    """Return the yaml path for a workflow name.

    User-defined workflows under ``<root>/workflows/<name>.yaml`` win
    over bundled. Falls back to bundled. If neither exists, returns
    the bundled path so the caller hits a clean WorkflowError.
    """
    if root is not None:
        user = root / "workflows" / f"{workflow_name}.yaml"
        if user.is_file():
            return user
    return WORKFLOWS_DIR / f"{workflow_name}.yaml"


def submit_job(
    *,
    job: JobConfig,
    workflow_path: Path | None = None,
    root: Path | None = None,
) -> Path:
    """Set up a job dir on disk. Returns the job dir path.

    Validates the workflow before writing any state. Snapshots
    workflow.yaml into the job dir. Copies the project repo into
    ``<job_dir>/repo`` if a project_repo_path is supplied.
    """
    wf_path = workflow_path or resolve_workflow_path(job.workflow_name, root=root)
    workflow = load_workflow(wf_path)
    assert workflow.name, "workflow must declare a name"

    job_dir = paths.ensure_job_layout(job.slug, root=root)
    shutil.copy(wf_path, paths.workflow_yaml(job.slug, root=root))

    now = _dt.datetime.now(_dt.UTC).isoformat()
    write_job_md(
        job_dir,
        {
            "slug": job.slug,
            "workflow": job.workflow_name,
            "state": "submitted",
            "submitted_at": now,
            "request": job.request_text,
        },
    )

    # Pre-create node dirs with empty state.md so the dashboard projection
    # has something to render before the orchestrator gets going.
    for node in workflow.nodes:
        nd = paths.ensure_node_layout(job.slug, node.id, root=root)
        state_path = nd / "state.md"
        if not state_path.exists():
            state_path.write_text("---\nstate: pending\n---\n")

    if job.project_repo_path is not None and job.project_repo_path.is_dir():
        dest = paths.repo_dir(job.slug, root=root)
        if not dest.exists():
            shutil.copytree(job.project_repo_path, dest, symlinks=False)

    # Initial control.md — operator-controlled lifecycle gate. The
    # orchestrator polls this between iterations to honor pause/cancel.
    control_path = paths.control_md(job.slug, root=root)
    if not control_path.exists():
        control_path.write_text(
            f"---\nstate: running\nrequested_at: {now}\nrequested_by: submit\n---\n"
        )
    return job_dir


def run_job(
    *,
    job: JobConfig,
    workflow_path: Path | None = None,
    root: Path | None = None,
    claude_binary: str = "claude",
    runner: ClaudeRunner = _default_runner,
) -> int:
    """Run a job to terminal state. Returns the orchestrator subprocess rc.

    Idempotent on resume: if the job dir already exists with a workflow
    snapshot, we reuse it. The orchestrator is itself idempotent
    (per-node state.md guards), so re-spawning it is safe.
    """
    wf_path = workflow_path or resolve_workflow_path(job.workflow_name, root=root)
    job_dir = paths.job_dir(job.slug, root=root)
    if not job_dir.is_dir():
        submit_job(job=job, workflow_path=wf_path, root=root)

    snapshot = paths.workflow_yaml(job.slug, root=root)
    prompt = render_orchestrator_prompt(
        job_dir=job_dir,
        workflow_path=snapshot,
        request_text=job.request_text,
    )

    now = _dt.datetime.now(_dt.UTC).isoformat()
    write_job_md(
        job_dir,
        {
            "slug": job.slug,
            "workflow": job.workflow_name,
            "state": "running",
            "submitted_at": now,
            "started_at": now,
            "request": job.request_text,
        },
    )

    cmd = [
        claude_binary,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "bypassPermissions",
    ]
    log.info("v2 runner: spawning claude orchestrator for job=%s", job.slug)
    completed = runner(
        cmd,
        job_dir,
        paths.orchestrator_jsonl(job.slug, root=root),
        paths.orchestrator_log(job.slug, root=root),
    )
    finished = _dt.datetime.now(_dt.UTC).isoformat()

    final_state = "completed" if completed.returncode == 0 else "failed"
    write_job_md(
        job_dir,
        {
            "slug": job.slug,
            "workflow": job.workflow_name,
            "state": final_state,
            "submitted_at": now,
            "started_at": now,
            "finished_at": finished,
            "request": job.request_text,
            "error": "" if completed.returncode == 0 else f"orchestrator rc={completed.returncode}",
        },
    )
    return completed.returncode


def discover_workflows(
    workflows_dir: Path = WORKFLOWS_DIR,
    *,
    user_workflows_dir: Path | None = None,
) -> list[Workflow]:
    """List bundled + user-defined workflows. User-defined wins on
    name collision (loaded second, overwrites in ``by_name`` map)."""
    by_name: dict[str, Workflow] = {}
    if workflows_dir.is_dir():
        for path in sorted(workflows_dir.glob("*.yaml")):
            try:
                wf = load_workflow(path)
                by_name[wf.name] = wf
            except Exception as exc:
                log.warning("workflow %s failed to load: %s", path, exc)
    if user_workflows_dir and user_workflows_dir.is_dir():
        for path in sorted(user_workflows_dir.glob("*.yaml")):
            try:
                wf = load_workflow(path)
                by_name[wf.name] = wf
            except Exception as exc:
                log.warning("user workflow %s failed to load: %s", path, exc)
    return list(by_name.values())
