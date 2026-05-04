"""Compute and persist :class:`JobCostSummary` on terminal job state.

The JobDriver calls :func:`write_job_summary` when a job reaches a
terminal state (COMPLETED / FAILED / ABANDONED) so post-hoc tooling has
a single, atomic landing site for the job's cost rollup. The model
docstring promises this; before this module the file was never written
and operators had to project rollups on demand from events.jsonl.

The folding logic mirrors
``dashboard/state/projections.py:_fold_cost_breakdown`` but returns a
typed :class:`JobCostSummary` and is callable from the JobDriver
process (which doesn't have a Cache).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from shared import paths
from shared.atomic import atomic_write_json
from shared.models.job import (
    AgentCostSummary,
    JobCostSummary,
    StageCostSummary,
)


def _stage_agent_refs(job_dir: Path) -> dict[str, str]:
    """Read stage-list.yaml → {stage_id: agent_ref}. Stages with no
    agent_ref (e.g. human stages) are still listed; agent_ref defaults
    to an empty string so downstream consumers can render them."""
    stage_list = job_dir / "stage-list.yaml"
    if not stage_list.is_file():
        return {}
    data = yaml.safe_load(stage_list.read_text()) or {}
    out: dict[str, str] = {}
    for s in data.get("stages", []):
        sid = s.get("id")
        if not isinstance(sid, str):
            continue
        ar = s.get("agent_ref")
        out[sid] = ar if isinstance(ar, str) else ""
    return out


def _stage_attempts(job_dir: Path, stage_id: str) -> int:
    """Number of attempts seen in stage.json (defaults to 1 if unreadable)."""
    sj = job_dir / "stages" / stage_id / "stage.json"
    if not sj.is_file():
        return 1
    try:
        return int(json.loads(sj.read_text()).get("attempt", 1))
    except (json.JSONDecodeError, ValueError, OSError):
        return 1


def compute_job_cost_summary(
    job_slug: str,
    *,
    job_id: str,
    project_slug: str,
    root: Path | None,
    completed_at: datetime | None = None,
) -> JobCostSummary:
    """Fold cost_accrued events from events.jsonl into a JobCostSummary."""
    job_dir = paths.job_dir(job_slug, root=root)
    events = paths.job_events_jsonl(job_slug, root=root)

    total_usd = 0.0
    total_tokens = 0
    # Per-stage rolling totals (filled lazily)
    stage_usd: dict[str, float] = {}
    stage_tokens: dict[str, int] = {}
    stage_subagents: dict[str, dict[str, float]] = {}
    # Per-agent rolling totals
    agent_invocations: dict[str, int] = {}
    agent_usd: dict[str, float] = {}
    agent_tokens: dict[str, int] = {}

    if events.is_file():
        with events.open("r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("event_type") != "cost_accrued":
                    continue
                payload = obj.get("payload") or {}
                usd_raw = payload.get("delta_usd")
                if not isinstance(usd_raw, int | float):
                    continue
                usd = float(usd_raw)
                tokens_raw = payload.get("delta_tokens")
                tokens = int(tokens_raw) if isinstance(tokens_raw, int) else 0

                total_usd += usd
                total_tokens += tokens

                sid = obj.get("stage_id")
                if isinstance(sid, str):
                    stage_usd[sid] = stage_usd.get(sid, 0.0) + usd
                    stage_tokens[sid] = stage_tokens.get(sid, 0) + tokens
                    sa = obj.get("subagent_id")
                    if isinstance(sa, str):
                        per = stage_subagents.setdefault(sid, {})
                        per[sa] = per.get(sa, 0.0) + usd

                ar = payload.get("agent_ref")
                if isinstance(ar, str):
                    agent_invocations[ar] = agent_invocations.get(ar, 0) + 1
                    agent_usd[ar] = agent_usd.get(ar, 0.0) + usd
                    agent_tokens[ar] = agent_tokens.get(ar, 0) + tokens

    agent_refs = _stage_agent_refs(job_dir)

    # Build per-stage summaries for every stage that either accrued cost
    # OR appears in the stage list. (A stage that ran but cost $0 still
    # gets a row; a stage that never ran is omitted.)
    stage_ids = set(stage_usd.keys()) | {
        sid for sid in agent_refs if (job_dir / "stages" / sid / "stage.json").exists()
    }
    by_stage = {
        sid: StageCostSummary(
            stage_id=sid,
            agent_ref=agent_refs.get(sid, ""),
            runs=_stage_attempts(job_dir, sid),
            total_usd=stage_usd.get(sid, 0.0),
            total_tokens=stage_tokens.get(sid, 0),
            by_subagent=stage_subagents.get(sid, {}),
        )
        for sid in sorted(stage_ids)
    }
    by_agent = {
        ar: AgentCostSummary(
            agent_ref=ar,
            invocations=agent_invocations[ar],
            total_usd=agent_usd[ar],
            total_tokens=agent_tokens[ar],
        )
        for ar in sorted(agent_invocations)
    }

    return JobCostSummary(
        job_id=job_id,
        project_slug=project_slug,
        total_usd=total_usd,
        total_tokens=total_tokens,
        by_stage=by_stage,
        by_agent=by_agent,
        completed_at=completed_at or datetime.now(UTC),
    )


def write_job_summary(
    job_slug: str,
    *,
    job_id: str,
    project_slug: str,
    root: Path | None,
    completed_at: datetime | None = None,
) -> Path:
    """Compute the summary and atomically write it to ``<job_dir>/job-summary.json``."""
    summary = compute_job_cost_summary(
        job_slug,
        job_id=job_id,
        project_slug=project_slug,
        root=root,
        completed_at=completed_at,
    )
    out = paths.job_dir(job_slug, root=root) / "job-summary.json"
    atomic_write_json(out, summary)
    return out
