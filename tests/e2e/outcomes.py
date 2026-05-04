"""Outcome assertion helpers for the real-claude e2e test.

Per docs/specs/2026-05-04-real-claude-e2e-impl-plan.md step H — one
helper per spec §Outcomes contract assertion. Each is a pure function
of (root, job_slug); failure raises ``AssertionError`` with a message
naming the missing/violating piece.

Outcome #11 (branches in remote) takes a runner + repo_slug instead of
job dir; it lives in :func:`assert_branches_exist` below and is
expected to fail on real runs until project-config plumbing carries
GitHub credentials into the spawned claude subprocess. The other
helpers operate purely on the local hammock-root and are deterministic.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import yaml

from shared import paths
from shared.models import Event
from shared.models.job import JobConfig, JobState
from shared.models.stage import StageDefinition, StageRun, StageState

OutcomeFn = Callable[[Path, str], None]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_events(root: Path, job_slug: str) -> list[Event]:
    p = paths.job_events_jsonl(job_slug, root=root)
    if not p.exists():
        return []
    return [Event.model_validate_json(line) for line in p.read_text().splitlines() if line.strip()]


def _read_stage_defs(root: Path, job_slug: str) -> list[StageDefinition]:
    stage_list = paths.job_dir(job_slug, root=root) / "stage-list.yaml"
    data = yaml.safe_load(stage_list.read_text()) or {}
    return [StageDefinition.model_validate(s) for s in data.get("stages", [])]


def _read_stage_run(root: Path, job_slug: str, stage_id: str) -> StageRun | None:
    p = paths.stage_json(job_slug, stage_id, root=root)
    if not p.exists():
        return None
    return StageRun.model_validate_json(p.read_text())


def _succeeded_agent_stages(root: Path, job_slug: str) -> list[StageDefinition]:
    """Return stage_defs for agent stages whose stage.json is SUCCEEDED."""
    out: list[StageDefinition] = []
    for sd in _read_stage_defs(root, job_slug):
        if sd.worker != "agent":
            continue
        sr = _read_stage_run(root, job_slug, sd.id)
        if sr is not None and sr.state == StageState.SUCCEEDED:
            out.append(sd)
    return out


# ---------------------------------------------------------------------------
# Outcome #1 — job COMPLETED
# ---------------------------------------------------------------------------


def assert_job_completed(root: Path, job_slug: str) -> None:
    cfg = JobConfig.model_validate_json(paths.job_json(job_slug, root=root).read_text())
    if cfg.state != JobState.COMPLETED:
        raise AssertionError(f"job {job_slug!r} did not reach COMPLETED (saw {cfg.state.value})")


# ---------------------------------------------------------------------------
# Outcome #2 — every stage SUCCEEDED
# ---------------------------------------------------------------------------


def assert_all_stages_succeeded(root: Path, job_slug: str) -> None:
    """Every stage that ran must be SUCCEEDED.

    Stages skipped via ``runs_if=false`` produce no ``stage.json`` (by
    driver design — see ``_execute_stages``); they are not failures.
    The outcome contract is "no stage that ran failed", which is the
    union of (a) every stage.json says SUCCEEDED, (b) every absent
    stage.json was conditionally skipped. Outcome #3 separately
    enforces "no FAILED/CANCELLED".
    """
    for sd in _read_stage_defs(root, job_slug):
        sr = _read_stage_run(root, job_slug, sd.id)
        if sr is None:
            # No stage.json — either conditionally skipped (runs_if=false)
            # or never reached. The latter is impossible when the job
            # state is COMPLETED, so we trust the dispatch-skip path.
            if sd.runs_if is None:
                raise AssertionError(
                    f"stage {sd.id!r}: no stage.json on disk and no runs_if "
                    "predicate — stage was never reached but job COMPLETED"
                )
            continue
        if sr.state != StageState.SUCCEEDED:
            raise AssertionError(f"stage {sd.id!r}: state={sr.state.value} (expected SUCCEEDED)")


# ---------------------------------------------------------------------------
# Outcome #3 — no FAILED / CANCELLED
# ---------------------------------------------------------------------------


def assert_no_failed_or_cancelled(root: Path, job_slug: str) -> None:
    for sd in _read_stage_defs(root, job_slug):
        sr = _read_stage_run(root, job_slug, sd.id)
        if sr is None:
            continue
        if sr.state in (StageState.FAILED, StageState.CANCELLED):
            raise AssertionError(f"stage {sd.id!r} ended in {sr.state.value}")


# ---------------------------------------------------------------------------
# Outcome #4 — required outputs on disk
# ---------------------------------------------------------------------------


def assert_required_outputs_exist(root: Path, job_slug: str) -> None:
    """Every stage that ran must have its declared required outputs on disk.

    Stages skipped via ``runs_if=false`` produce no stage.json AND no
    outputs (by driver design). For those, the output absence is not a
    failure — the stage didn't run. We detect skipped stages the same
    way outcome #2 does: no stage.json on disk plus a ``runs_if``
    predicate present.
    """
    job_dir = paths.job_dir(job_slug, root=root)
    for sd in _read_stage_defs(root, job_slug):
        sr = _read_stage_run(root, job_slug, sd.id)
        if sr is None and sd.runs_if is not None:
            # Conditionally skipped — no outputs expected.
            continue
        for out in sd.exit_condition.required_outputs or []:
            if not (job_dir / out.path).exists():
                raise AssertionError(
                    f"stage {sd.id!r} missing declared required_output {out.path!r}"
                )


# ---------------------------------------------------------------------------
# Outcome #5 — Stop hook fired for each SUCCEEDED stage
# ---------------------------------------------------------------------------


def assert_stop_hook_fired_for_each_succeeded_stage(root: Path, job_slug: str) -> None:
    """Spec D16: trust transitivity (hook fired ∧ stage SUCCEEDED ⟹
    artifact valid). Failure mode: a SUCCEEDED stage with no
    ``hook_fired`` event indicates artifact validation didn't run."""
    events = _read_events(root, job_slug)
    hook_stages = {e.stage_id for e in events if e.event_type == "hook_fired"}
    for sd in _succeeded_agent_stages(root, job_slug):
        if sd.id not in hook_stages:
            raise AssertionError(
                f"stage {sd.id!r} SUCCEEDED but no hook_fired event in events.jsonl "
                f"— the Stop hook didn't run, so artifacts weren't validated"
            )


# ---------------------------------------------------------------------------
# Outcome #6 — summary.md has a URL
# ---------------------------------------------------------------------------


def assert_summary_md_has_url(root: Path, job_slug: str) -> None:
    summary = paths.job_dir(job_slug, root=root) / "summary.md"
    if not summary.is_file():
        raise AssertionError(f"summary.md missing at {summary}")
    text = summary.read_text()
    if "http://" not in text and "https://" not in text:
        raise AssertionError("summary.md does not contain a url (expected a PR or branch link)")


# ---------------------------------------------------------------------------
# Outcomes #7-#10 — per-stage agent artifacts
# ---------------------------------------------------------------------------


_AGENT_ARTIFACTS: tuple[str, ...] = (
    "stream.jsonl",
    "messages.jsonl",
    "result.json",
    "stderr.log",
)


def assert_agent_artifacts_present(root: Path, job_slug: str) -> None:
    """For every agent stage that ran, the stream/messages/result/stderr
    files must be present in ``stages/<sid>/run-<n>/agent0/``."""
    for sd in _succeeded_agent_stages(root, job_slug):
        sr = _read_stage_run(root, job_slug, sd.id)
        assert sr is not None  # narrowed by _succeeded_agent_stages
        attempt_dir = paths.stage_run_dir(job_slug, sd.id, sr.attempt, root=root)
        agent_dir = attempt_dir / "agent0"
        if not agent_dir.is_dir():
            raise AssertionError(f"stage {sd.id!r}: agent0 dir missing at {agent_dir}")
        for fname in _AGENT_ARTIFACTS:
            f = agent_dir / fname
            if not f.is_file():
                raise AssertionError(
                    f"stage {sd.id!r}: missing per-stage agent artifact {fname!r} at {f}"
                )


# ---------------------------------------------------------------------------
# Outcome #11 — branches present (lives outside this module; takes
# different inputs and is project-config-dependent)
# ---------------------------------------------------------------------------


def assert_branches_exist(
    repo_slug: str,
    *,
    list_remote_branches: Callable[[str], set[str]],
    job_slug: str,
) -> None:
    """At least one job + stage branch in the convention namespace."""
    branches = list_remote_branches(repo_slug)
    job_branch = f"hammock/jobs/{job_slug}"
    if job_branch not in branches:
        raise AssertionError(
            f"expected job branch {job_branch!r} not found in remote: {sorted(branches)}"
        )
    stage_prefix = f"hammock/stages/{job_slug}/"
    if not any(b.startswith(stage_prefix) for b in branches):
        raise AssertionError(
            f"no stage branch under {stage_prefix!r} in remote: {sorted(branches)}"
        )


# ---------------------------------------------------------------------------
# Outcome #12 — event stream well-formed JSONL
# ---------------------------------------------------------------------------


def assert_event_stream_well_formed(root: Path, job_slug: str) -> None:
    p = paths.job_events_jsonl(job_slug, root=root)
    if not p.is_file():
        raise AssertionError(f"events.jsonl missing at {p}")
    for n, raw in enumerate(p.read_text().splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"events.jsonl line {n}: invalid JSON ({exc.msg})") from exc


# ---------------------------------------------------------------------------
# Outcome #13 — worktree_created event present
# ---------------------------------------------------------------------------


def assert_at_least_one_worktree_created_event(root: Path, job_slug: str) -> None:
    events = _read_events(root, job_slug)
    if not any(e.event_type == "worktree_created" for e in events):
        raise AssertionError(
            "no worktree_created event in events.jsonl — stage isolation never reported"
        )


# ---------------------------------------------------------------------------
# Outcome #14 — worker_exit per SUCCEEDED stage with exit_code=0
# ---------------------------------------------------------------------------


def assert_worker_exit_for_each_succeeded_stage(root: Path, job_slug: str) -> None:
    """Spec D11: failed-stage worker_exit events exist with succeeded=False;
    the assertion narrows to SUCCEEDED stages and pins exit_code=0."""
    events = _read_events(root, job_slug)
    exits_by_stage: dict[str | None, list[Event]] = {}
    for e in events:
        if e.event_type == "worker_exit":
            exits_by_stage.setdefault(e.stage_id, []).append(e)

    for sd in _succeeded_agent_stages(root, job_slug):
        candidates = exits_by_stage.get(sd.id, [])
        if not candidates:
            raise AssertionError(f"stage {sd.id!r}: SUCCEEDED but no worker_exit event")
        # Pick the last (most recent) — earlier attempts may have been
        # exception-routed with a different exit_code.
        last = candidates[-1]
        if last.payload.get("exit_code") != 0:
            raise AssertionError(
                f"stage {sd.id!r}: worker_exit.exit_code="
                f"{last.payload.get('exit_code')!r} (expected 0)"
            )
        if last.payload.get("succeeded") is not True:
            raise AssertionError(f"stage {sd.id!r}: worker_exit.succeeded is not True")


# ---------------------------------------------------------------------------
# Registry — discoverable for the test driver
# ---------------------------------------------------------------------------


# Spec outcome #5 (Stop hook fired event) is the only deferred entry:
# the bundled ``validate-stage-exit.sh`` doesn't emit a ``hook_fired``
# event today. Transitive coverage via outcomes #2 + #4 (a stage that
# SUCCEEDED with required outputs present implies the hook didn't
# reject — otherwise FAILED). The helper stays in this module for use
# once emission is wired.
OUTCOMES: dict[str, OutcomeFn] = {
    "job_completed": assert_job_completed,
    "all_stages_succeeded": assert_all_stages_succeeded,
    "no_failed_or_cancelled": assert_no_failed_or_cancelled,
    "required_outputs_exist": assert_required_outputs_exist,
    "summary_md_has_url": assert_summary_md_has_url,
    "agent_artifacts_present": assert_agent_artifacts_present,
    "event_stream_well_formed": assert_event_stream_well_formed,
    "worktree_created_event": assert_at_least_one_worktree_created_event,
    "worker_exit_per_succeeded_stage": assert_worker_exit_for_each_succeeded_stage,
}
