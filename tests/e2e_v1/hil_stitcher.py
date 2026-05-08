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
import re
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
        "document": "## Review\n\nauto-approved by test stitcher",
    }


def merge_pr_then_submit_review(
    *,
    pending: PendingHil,
    workflow: Workflow,
    job_slug: str,
    root: Path,
    var_name: str,
    **_: object,
) -> dict[str, Any]:
    """Answer policy for `pr-review-verdict`-typed gates.

    Per design-patch §9.4: the human submits ONLY ``{verdict: "merged"}``;
    the engine's pr-review-verdict.produce verifies via ``gh pr view``.
    The stitcher first ensures the PR is actually merged on GitHub
    (idempotent) so the engine's verification succeeds.
    """
    # Find the pr envelope referenced by this node's inputs. The HIL
    # node may declare its upstream `pr`-typed input under any name
    # (e.g. ``$pr-merged-loop.pr[i]`` inside the loop, or ``$tests_pr``
    # at top level). We walk the node's declared inputs to find the
    # one that resolves to a `pr`-typed variable, then read its
    # envelope (loop-indexed when applicable).
    pr_url = _find_pr_url_for_node(pending=pending, workflow=workflow, job_slug=job_slug, root=root)
    if pr_url is None:
        raise RuntimeError(
            f"merge_pr_then_submit_review: no `pr`-typed input envelope "
            f"found for node {pending.node_id!r} (iter_path={pending.iter_path!r})"
        )

    # Idempotent merge: check current state first; skip if already MERGED.
    import os as _os

    gh_env = {**_os.environ, "NO_COLOR": "1"}
    state_check = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "state", "--jq", ".state"],
        capture_output=True,
        text=True,
        check=False,
        env=gh_env,
    )
    current_state = state_check.stdout.strip() if state_check.returncode == 0 else ""

    if current_state != "MERGED":
        result = subprocess.run(
            ["gh", "pr", "merge", pr_url, "--squash", "--admin"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"merge_pr_then_submit_review: `gh pr merge {pr_url}` failed: "
                f"{result.stderr.strip()}"
            )

    # Submission shape per pr-review-verdict: just the verdict.
    return {"verdict": "merged"}


def _find_pr_url_for_node(
    *,
    pending: PendingHil,
    workflow: Workflow,
    job_slug: str,
    root: Path,
) -> str | None:
    """Resolve the upstream PR url for a HIL node by walking its inputs.

    The HIL node's input may reference a top-level pr variable
    (``$tests_pr``) or a loop-indexed one (``$pr-merged-loop.pr[i]``).
    We walk ``node.inputs``, keep only inputs whose target variable's
    type is ``pr``, and read the matching envelope.
    """
    node = _find_node_by_id(workflow, pending.node_id)
    if node is None:
        return None

    for _input_name, ref in (node.inputs or {}).items():
        var_name, idx_form = _parse_pr_ref(ref)
        if var_name is None:
            continue
        if workflow.variables.get(var_name, _Sentinel()).type != "pr":
            continue
        # Loop-indexed reference like ``$loop-id.pr[i]`` resolves
        # against the pending marker's iter_path. ``[i]`` reads the
        # current iteration; ``[i-1]`` the previous one.
        if idx_form is not None:
            iter_path = pending.iter_path
            if not iter_path:
                continue
            if idx_form == "i-1":
                if iter_path[-1] <= 0:
                    continue
                resolved = (*iter_path[:-1], iter_path[-1] - 1)
            else:
                resolved = iter_path
            env_path = paths.variable_envelope_path(job_slug, var_name, resolved, root=root)
        else:
            env_path = paths.variable_envelope_path(job_slug, var_name, root=root)
        if env_path.is_file():
            env = Envelope.model_validate_json(env_path.read_text())
            url = env.value.get("url") if isinstance(env.value, dict) else None
            if isinstance(url, str) and url:
                return url
    return None


def _find_node_by_id(workflow: Workflow, node_id: str):  # type: ignore[no-untyped-def]
    """DFS through workflow.nodes (top-level + loop bodies) for an id."""
    stack: list[Any] = list(workflow.nodes)
    while stack:
        node = stack.pop()
        if getattr(node, "id", None) == node_id:
            return node
        for inner in getattr(node, "body", []) or []:
            stack.append(inner)
    return None


_PLAIN_REF = re.compile(r"^\$([A-Za-z][A-Za-z0-9_-]*)$")
_LOOP_REF = re.compile(r"^\$([A-Za-z][A-Za-z0-9_-]*)\.([A-Za-z][A-Za-z0-9_-]*)\[(i|i-1|last)\]$")


def _parse_pr_ref(ref: str) -> tuple[str | None, str | None]:
    """Parse a $var or $loop.var[idx] reference. Returns
    (var_name, idx_form) where idx_form is None for plain refs,
    "i"/"i-1"/"last" for loop-indexed refs, or (None, None) on miss."""
    text = ref.strip()
    m = _LOOP_REF.match(text)
    if m is not None:
        return m.group(2), m.group(3)
    m = _PLAIN_REF.match(text)
    if m is not None:
        return m.group(1), None
    return None, None


class _Sentinel:
    type = ""


# Back-compat alias (some test wiring may still import the old name);
# kept until all callers migrate, then deletable.
merge_pr_then_confirm = merge_pr_then_submit_review


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
        self._answered: set[tuple[str, tuple[int, ...], str | None]] = set()
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
                # Use a key that includes the full iter_path AND the marker's
                # created_at timestamp. The timestamp distinguishes
                # successive instances of the same (node_id, iter_path)
                # gate — an outer count loop re-enters its inner loop and
                # re-creates the marker with a fresh created_at.
                answer_key = (
                    item.node_id,
                    item.iter_path,
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
                            iter_path=item.iter_path,
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
                        "stitcher: answered HIL gate %s (iter_path=%s)",
                        item.node_id,
                        item.iter_path,
                    )
                else:
                    log.warning(
                        "stitcher: gate %s (iter_path=%s) had errors; will retry on next poll",
                        item.node_id,
                        item.iter_path,
                    )
            time.sleep(self.poll_interval)
