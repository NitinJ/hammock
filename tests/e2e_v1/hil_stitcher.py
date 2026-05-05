"""HIL stitcher — test helper that watches a job's pending markers and
auto-submits typed answers via the public submission API.

Per IMPL patch §4.2: tests submit via the same public API a human would
use (no reaching into engine internals). Each pending marker is matched
to a pre-registered "answer policy" by node id; the policy decides what
typed payload to submit.

Runs in a background thread; the test's main thread runs the driver
(which itself blocks on each HIL gate until the marker is removed).
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from engine.v1.hil import (
    HilSubmissionError,
    PendingHil,
    list_pending,
    submit_hil_answer,
)
from shared.v1 import paths
from shared.v1.envelope import Envelope
from shared.v1.workflow import Workflow

log = logging.getLogger(__name__)


# Answer policy signature. Receives the full PendingHil (so policies can
# read loop context if they need it), the workflow, the job slug, the
# root path, and the variable name being submitted. Returns the typed
# payload dict.
AnswerPolicy = Callable[..., dict[str, Any]]


def approve_review_verdict(**_: object) -> dict[str, Any]:
    """Default answer policy for `review-verdict`-typed gates: approve
    with a generic summary."""
    return {
        "verdict": "approved",
        "summary": "auto-approved by test stitcher",
        "unresolved_concerns": [],
        "addressed_in_this_iteration": [],
    }


def merge_pr_then_confirm(
    *,
    pending: PendingHil,
    workflow: Workflow,
    job_slug: str,
    root: Path,
    var_name: str,
    **_: object,
) -> dict[str, Any]:
    """Answer policy for `pr-merge-confirmation`-typed gates.

    1. Reads the upstream `pr` variable (loop-indexed) to get the URL.
    2. Actually merges the PR on GitHub via `gh pr merge --squash --admin`.
    3. Returns ``{"pr_url": <url>}`` for the engine's
       pr-merge-confirmation type to verify.
    """
    # Find the pr envelope this iteration produced — look for the
    # loop-indexed `pr` envelope first; fall back to plain.
    pr_url: str | None = None
    if pending.loop_id is not None and pending.iteration is not None:
        pr_path = paths.loop_variable_envelope_path(
            job_slug, pending.loop_id, "pr", pending.iteration, root=root
        )
        if pr_path.is_file():
            pr_env = Envelope.model_validate_json(pr_path.read_text())
            pr_url = pr_env.value.get("url")
    if pr_url is None:
        # Plain pr fallback (T3-shape).
        plain_pr = paths.variable_envelope_path(job_slug, "pr", root=root)
        if plain_pr.is_file():
            pr_env = Envelope.model_validate_json(plain_pr.read_text())
            pr_url = pr_env.value.get("url")
    if pr_url is None:
        raise RuntimeError(
            f"merge_pr_then_confirm: no `pr` envelope found for node "
            f"{pending.node_id!r} (loop_id={pending.loop_id!r}, "
            f"iteration={pending.iteration!r})"
        )

    # Idempotent: check current state first. If already MERGED (e.g. a
    # prior stitcher iteration succeeded but submit_hil_answer failed
    # downstream), skip the merge call. Saves us a second `gh pr merge`
    # which fails with "already merged".
    import os as _os

    env = {**_os.environ, "NO_COLOR": "1"}
    state_check = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "state", "--jq", ".state"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    current_state = state_check.stdout.strip() if state_check.returncode == 0 else ""

    if current_state != "MERGED":
        # Merge the PR on GitHub. --admin lets the user merge their own PR
        # even when branch protection requires reviews; --squash keeps
        # history tidy. We don't pass --delete-branch — teardown handles
        # branch cleanup.
        result = subprocess.run(
            ["gh", "pr", "merge", pr_url, "--squash", "--admin"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"merge_pr_then_confirm: `gh pr merge {pr_url}` failed: {result.stderr.strip()}"
            )

    return {"pr_url": pr_url}


class HilStitcher:
    """Background watcher that polls a job's pending dir and submits
    answers as soon as gates appear.

    Usage:

        stitcher = HilStitcher(
            job_slug=job_slug,
            workflow=workflow,
            root=root,
            policies={"review-design-spec-human": approve_review_verdict},
        )
        stitcher.start()
        try:
            run_job(...)  # may block on HIL; stitcher unblocks it
        finally:
            stitcher.stop()
    """

    def __init__(
        self,
        *,
        job_slug: str,
        workflow: Workflow,
        root: Path,
        policies: dict[str, AnswerPolicy],
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self.job_slug = job_slug
        self.workflow = workflow
        self.root = root
        self.policies = policies
        self.poll_interval = poll_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._answered: set[tuple[str, str | None, int | None]] = set()
        self.errors: list[str] = []

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="hil-stitcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                pending = list_pending(self.job_slug, root=self.root)
            except Exception as exc:
                self.errors.append(f"list_pending failed: {exc}")
                time.sleep(self.poll_interval)
                continue

            for item in pending:
                # Use a key that includes loop iteration AND the marker's
                # created_at timestamp. The timestamp distinguishes
                # successive instances of the same (node_id, loop_id, iter)
                # gate — an outer count loop re-enters its inner loop and
                # re-creates the marker with a fresh created_at.
                answer_key = (
                    item.node_id,
                    item.loop_id,
                    item.iteration,
                    item.created_at,
                )
                if answer_key in self._answered:
                    continue
                policy = self.policies.get(item.node_id)
                if policy is None:
                    self.errors.append(f"no answer policy registered for node {item.node_id!r}")
                    continue

                # Submit each declared output variable. Track whether
                # ANY submission for this gate hit an error — if so, do
                # NOT mark the gate as answered (the engine still has
                # the pending marker on disk; we retry next poll).
                gate_succeeded = True
                for var_name in item.output_var_names:
                    try:
                        payload = policy(
                            pending=item,
                            workflow=self.workflow,
                            job_slug=self.job_slug,
                            root=self.root,
                            var_name=var_name,
                        )
                    except Exception as exc:
                        self.errors.append(f"policy for {item.node_id}/{var_name} raised: {exc}")
                        gate_succeeded = False
                        continue
                    try:
                        submit_hil_answer(
                            job_slug=self.job_slug,
                            node_id=item.node_id,
                            var_name=var_name,
                            value_payload=payload,
                            root=self.root,
                            workflow=self.workflow,
                        )
                    except HilSubmissionError as exc:
                        self.errors.append(
                            f"submission for {item.node_id}/{var_name} failed: {exc}"
                        )
                        gate_succeeded = False
                        continue

                if gate_succeeded:
                    self._answered.add(answer_key)
                    log.info(
                        "stitcher: answered HIL gate %s (loop=%s iter=%s)",
                        item.node_id,
                        item.loop_id,
                        item.iteration,
                    )
                else:
                    log.warning(
                        "stitcher: gate %s (loop=%s iter=%s) had errors; will retry on next poll",
                        item.node_id,
                        item.loop_id,
                        item.iteration,
                    )
            time.sleep(self.poll_interval)
