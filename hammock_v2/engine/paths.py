"""Job dir layout helpers for Hammock v2.

Single source of truth for the on-disk shape:

    <root>/jobs/<slug>/
    ├── job.md
    ├── workflow.yaml
    ├── orchestrator.jsonl
    ├── orchestrator.log
    ├── repo/                           (optional, project clone)
    └── nodes/<node_id>/
        ├── input.md
        ├── prompt.md
        ├── output.md
        ├── state.md
        ├── chat.jsonl
        ├── awaiting_human.md           (optional, written by orchestrator)
        └── human_decision.md           (optional, written by dashboard HIL)
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_ROOT = Path.home() / ".hammock-v2"


def resolve_root(root: Path | None = None) -> Path:
    return root or DEFAULT_ROOT


def jobs_dir(root: Path | None = None) -> Path:
    return resolve_root(root) / "jobs"


def job_dir(slug: str, root: Path | None = None) -> Path:
    return jobs_dir(root) / slug


def job_md(slug: str, root: Path | None = None) -> Path:
    return job_dir(slug, root) / "job.md"


def workflow_yaml(slug: str, root: Path | None = None) -> Path:
    return job_dir(slug, root) / "workflow.yaml"


def orchestrator_jsonl(slug: str, root: Path | None = None) -> Path:
    return job_dir(slug, root) / "orchestrator.jsonl"


def orchestrator_log(slug: str, root: Path | None = None) -> Path:
    return job_dir(slug, root) / "orchestrator.log"


def repo_dir(slug: str, root: Path | None = None) -> Path:
    return job_dir(slug, root) / "repo"


def inputs_dir(slug: str, root: Path | None = None) -> Path:
    """Operator-attached artifacts (uploaded at submit time)."""
    return job_dir(slug, root) / "inputs"


def nodes_dir(slug: str, root: Path | None = None) -> Path:
    return job_dir(slug, root) / "nodes"


def node_dir(slug: str, node_id: str, root: Path | None = None) -> Path:
    return nodes_dir(slug, root) / node_id


def node_input(slug: str, node_id: str, root: Path | None = None) -> Path:
    return node_dir(slug, node_id, root) / "input.md"


def node_prompt(slug: str, node_id: str, root: Path | None = None) -> Path:
    return node_dir(slug, node_id, root) / "prompt.md"


def node_output(slug: str, node_id: str, root: Path | None = None) -> Path:
    return node_dir(slug, node_id, root) / "output.md"


def node_state(slug: str, node_id: str, root: Path | None = None) -> Path:
    return node_dir(slug, node_id, root) / "state.md"


def node_chat_jsonl(slug: str, node_id: str, root: Path | None = None) -> Path:
    return node_dir(slug, node_id, root) / "chat.jsonl"


def node_awaiting_human(slug: str, node_id: str, root: Path | None = None) -> Path:
    return node_dir(slug, node_id, root) / "awaiting_human.md"


def node_human_decision(slug: str, node_id: str, root: Path | None = None) -> Path:
    return node_dir(slug, node_id, root) / "human_decision.md"


def ensure_job_layout(slug: str, root: Path | None = None) -> Path:
    """Create the job dir + nodes/ skeleton. Idempotent."""
    jd = job_dir(slug, root)
    jd.mkdir(parents=True, exist_ok=True)
    nodes_dir(slug, root).mkdir(parents=True, exist_ok=True)
    return jd


def ensure_node_layout(slug: str, node_id: str, root: Path | None = None) -> Path:
    """Create a node dir. Idempotent."""
    nd = node_dir(slug, node_id, root)
    nd.mkdir(parents=True, exist_ok=True)
    return nd


def ensure_inputs_dir(slug: str, root: Path | None = None) -> Path:
    """Create the inputs/ dir for operator-attached artifacts. Idempotent."""
    d = inputs_dir(slug, root)
    d.mkdir(parents=True, exist_ok=True)
    return d
