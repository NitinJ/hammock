"""End-to-end test: run a Hammock v1 workflow against real Claude + real
GitHub. Parameterised over the YAMLs in ``workflows/`` so every stage
T1..T6 runs the same harness with progressively richer YAML.

This test is opt-in: skips unless ``HAMMOCK_E2E_REAL_CLAUDE=1``. It costs
real LLM tokens.
"""

from __future__ import annotations

import datetime as _dt
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from engine.v1.driver import run_job, submit_job
from engine.v1.loader import load_workflow
from tests.e2e_v1 import outcomes
from tests.e2e_v1.bootstrap import bootstrap_test_repo
from tests.e2e_v1.cleanup import take_snapshot, teardown
from tests.e2e_v1.hil_stitcher import (
    HilStitcher,
    approve_review_verdict,
    merge_pr_then_confirm,
)
from tests.e2e_v1.preflight import (
    PreflightFailure,
    PreflightSkip,
    _slug_from_url,
    run_preflight,
)

_WORKFLOWS_DIR = Path(__file__).parent / "workflows"
_SEED_DIR = Path(__file__).parent / "seed_test_repo"


def _request_text_for(workflow_yaml: str) -> str:
    """Per-stage request text. The agent's first node consumes this."""
    if workflow_yaml in {"T1.yaml", "T2.yaml"}:
        return (
            "There is a function `add_integers(*nums)` in a Python project. "
            "When called with no arguments, it returns `None` instead of `0`. "
            "Write a structured bug report describing the issue."
        )
    if workflow_yaml in {"T3.yaml", "T4.yaml"}:
        return (
            "There is a bug in `add_integers.py`: the function `add_integers(*nums)` "
            "returns `None` when called with no arguments. It should return `0` "
            "(the additive identity, matching `sum(())`). "
            "Write a bug report, design a fix, and implement it. "
            "The fix should remove the `if not nums: return None` guard. "
            "Keep the function variadic (`*nums: int`)."
        )
    if workflow_yaml == "T6.yaml":
        return (
            "There is a bug in `add_integers.py`: the function `add_integers(*nums)` "
            "returns `None` when called with no arguments. It should return `0` "
            "(the additive identity, matching `sum(())`). "
            "Plan a single-stage fix: remove the `if not nums: return None` "
            "guard and keep the function variadic (`*nums: int`). "
            "When you write the impl plan, set `count: 1` (one stage of "
            "implementation work). Provide a `stages` list with one entry "
            "describing this fix."
        )
    if workflow_yaml == "T5.yaml":
        return (
            "There are TWO independent improvements needed in `add_integers.py`. "
            "(1) Bug fix: `add_integers(*nums)` returns `None` when called with "
            "no arguments — it should return `0` (the additive identity, matching "
            "`sum(())`). Remove the `if not nums: return None` guard; keep the "
            "function variadic (`*nums: int`). "
            "(2) Documentation: the function lacks a docstring. Add a clear, "
            "concise docstring describing what the function does and what it "
            "returns. "
            "These two improvements MUST be split across separate PRs (one fix "
            "per PR). The workflow will run an implement loop twice; each "
            "iteration should make exactly ONE of the two changes. Inspect the "
            "current state of the file in the worktree to determine which "
            "improvement is still pending, and apply that one."
        )
    raise ValueError(f"no request text registered for {workflow_yaml!r}")


# Per-stage HIL answer policies. Each entry maps a workflow YAML to a
# {node_id: policy} dict. The stitcher invokes the policy when it sees
# the matching node's pending marker.
_HIL_POLICIES: dict[str, dict[str, Callable[..., dict[str, Any]]]] = {
    "T2.yaml": {
        "review-design-spec-human": approve_review_verdict,
    },
    "T3.yaml": {
        "review-design-spec-human": approve_review_verdict,
    },
    "T4.yaml": {
        "review-design-spec-human": approve_review_verdict,
        "pr-merge-hil": merge_pr_then_confirm,
    },
    "T5.yaml": {
        "review-design-spec-human": approve_review_verdict,
        "pr-merge-hil": merge_pr_then_confirm,
    },
    "T6.yaml": {
        "review-design-spec-human": approve_review_verdict,
        "review-impl-spec-human": approve_review_verdict,
        "review-impl-plan-human": approve_review_verdict,
        "pr-merge-hil": merge_pr_then_confirm,
        "tests-pr-merge-hil": merge_pr_then_confirm,
    },
}


def _timeout_seconds() -> int:
    raw = os.environ.get("HAMMOCK_E2E_TIMEOUT_MIN", "30")
    try:
        return int(raw) * 60
    except ValueError:
        return 30 * 60


@pytest.mark.real_claude_v1
@pytest.mark.timeout(_timeout_seconds())
@pytest.mark.parametrize(
    "workflow_yaml",
    [
        "T1.yaml",
        "T2.yaml",
        "T3.yaml",
        "T4.yaml",
        "T5.yaml",
        "T6.yaml",
    ],
)
def test_workflow(tmp_path: Path, workflow_yaml: str) -> None:
    # 1. Preflight — skip when opt-in unset; fail when set but config bad.
    try:
        cfg = run_preflight(env=os.environ)
    except PreflightSkip as exc:
        pytest.skip(str(exc))
    except PreflightFailure as exc:
        pytest.fail(str(exc))

    repo_slug = _slug_from_url(cfg.repo_url)

    # 2. Bootstrap (idempotent — reuses single hammock-e2e-test repo).
    bootstrap_test_repo(cfg.repo_url, seed_dir=_SEED_DIR)

    # 3. Snapshot pre-existing branches so teardown only deletes the diff.
    snapshot = take_snapshot(repo_slug)

    root = tmp_path / "hammock-root"
    root.mkdir()

    workflow_path = _WORKFLOWS_DIR / workflow_yaml
    request_text = _request_text_for(workflow_yaml)

    # Use a date-prefixed slug so multiple test runs in the same day stay
    # readable in the operator's tmp dir if --keep-root.
    job_slug = (
        f"{_dt.datetime.now(_dt.UTC).strftime('%Y-%m-%d')}-"
        f"{workflow_yaml.removesuffix('.yaml').lower()}"
    )

    workflow = load_workflow(workflow_path)
    stitcher: HilStitcher | None = None
    policies = _HIL_POLICIES.get(workflow_yaml, {})

    try:
        # 4. Submit job (validates + writes JobConfig + seeds request var).
        # If the workflow has code-kind nodes, submit_job also clones the
        # test repo and creates the job branch.
        submit_job(
            workflow_path=workflow_path,
            request_text=request_text,
            job_slug=job_slug,
            root=root,
            repo_url=cfg.repo_url,
            repo_slug=repo_slug,
        )

        # 5. Stitcher (only when the workflow has HIL gates).
        if policies:
            stitcher = HilStitcher(
                job_slug=job_slug,
                workflow=workflow,
                root=root,
                policies=policies,
                poll_interval_seconds=1.0,
            )
            stitcher.start()

        # 6. Drive to terminal. The driver waits in-process on each HIL
        # gate; the stitcher submits answers from another thread.
        run_job(
            job_slug=job_slug,
            root=root,
            hil_poll_interval_seconds=1.0,
            hil_timeout_seconds=float(_timeout_seconds()),
        )

        # 7. Outcome assertions — every variable-shaped contract.
        for fn in outcomes.OUTCOMES.values():
            fn(root, job_slug, workflow)

        # 8. Stitcher must not have recorded any errors.
        if stitcher is not None:
            assert stitcher.errors == [], f"HIL stitcher recorded errors: {stitcher.errors}"

    finally:
        if stitcher is not None:
            stitcher.stop()
        teardown(
            root=root,
            repo_slug=repo_slug,
            snapshot=snapshot,
            keep_root=cfg.keep_root,
        )
