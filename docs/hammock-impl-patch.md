# Hammock implementation patch

The single source of truth for implementation work. Captures *what to build, in what order, with what tests*. Decisions originally written in `docs/hammock-design-patch.md` §9 are folded in here so an implementor can work from this document alone.

The design-patch remains the rationale archive. This doc is the work order.

---

## Guiding principles

1. **Test first, at every level.** Two layers, both written before the code they cover:
   - **Unit tests** for each module — written first, drive the module's behaviour.
   - **End-to-end / integration tests** for the system — written first, assert the whole flow.
2. **Iterate on data shape, not code structure.** Once unit tests pass for a module, run integration against the smallest meaningful disk state. Add complexity one capability at a time.
3. **Throw away aggressively.** Code that holds the wrong shape gets rewritten, not patched.
4. **Real Claude + real GitHub for the engine.** Engine e2e (`tests/e2e_v1/`) hits the real APIs. Dashboard tests do not — disk is the only contract between engine and dashboard, so dashboard tests script disk state and run offline.
5. **KISS / YAGNI.** Two of us, zero users, pre-funding. No production-scale infrastructure, no caches without a measured workload, no abstractions for hypothetical futures.

---

## Methodology — apply to every feature, bug, and ask

Standard workflow for *any* implementation work in this repo. Every stage below applies it.

### Step 0 — Interfaces / stubs first

Before writing any logic, write the shape of every module, class, function, and type the change introduces or modifies.

- Function signatures with full type hints; raise `NotImplementedError` in the body.
- Class skeletons with method signatures and docstrings; no implementation.
- New Pydantic models with all fields and types.
- New module files with their public exports declared.

Output: files where `python -c "import ..."` succeeds and `mypy` passes, but every behavioural call raises `NotImplementedError`. Commit on a branch.

### Step 1 — Failing e2e + integration tests, against the Step 0 stubs

Write the e2e and integration tests **before any implementation**. Tests reference Step 0 stubs directly; running them produces `NotImplementedError` (proves the test reaches the contract surface) or behavioural assertion failures (proves the test asserts the right thing).

These tests describe **expected behaviour**, not the path the implementation will take. They are the spec. Frozen for the duration of Step 3.

### Step 2 — Implementation + unit tests, per component

For each Step 0 stub, in dependency order:

1. Write unit tests for that one component's public surface.
2. Implement the component until those unit tests pass.
3. Run the full unit-test suite — must stay green.
4. Move to the next component.

Unit tests are the implementor's tool. They drive the design of one component. Step 1 tests are the spec's tool — not consulted during Step 2.

### Step 3 — Test → fix loop, against the Step 1 tests

When all components are unit-tested and green, run the Step 1 e2e + integration tests. Whatever fails, fix in the implementation — not in the test.

**Never modify a Step 1 test during Step 3 without asking.** The Step 1 tests are the spec. Editing them mid-fix is how the implementor moves the goalposts. If a Step 1 test really is wrong (the spec was incorrect, an interface decision changed), surface it explicitly and get sign-off. The default answer is "fix the code, not the test."

### Why this order

- Step 0 makes the contract concrete before tests or code are written. Without it, tests drift toward whatever shape the implementation grew into.
- Step 1 forces the spec to exist as runnable code before any implementation. Without it, "what should this do" becomes "what did this end up doing."
- Step 2 keeps each module independently verified.
- Step 3's no-edit rule is the honesty mechanism. The whole sequence collapses if the implementor can rewrite the spec when the spec is inconvenient.

Skipping this on the grounds of "this one is small" is how the cycle that v0 fell into starts again.

---

## Landing — PR cadence per stage

One PR per stage. No exceptions. The flow is identical for every stage from Stage 1 onward:

1. **Branch** — work happens on `stage-N-<slug>` (e.g. `stage-1-integration-harness`).
2. **Implement** — apply Steps 0 → 1 → 2 → 3 of the Methodology on that branch. All commits land locally on the branch as work progresses.
3. **Pre-PR local CI mirror — MANDATORY before pushing.** Run every check that CI runs, locally, against the staged branch. If any fails, fix and re-run before pushing. Do NOT push to open a PR until all of these are green:
   - `uv run ruff check .` — lint
   - `uv run ruff format --check .` — formatter (use `ruff format .` to auto-fix, then re-check)
   - `uv run pyright shared/ dashboard/` — strict type check
   - `uv run pytest tests/ -q` (excluding `tests/e2e/` and `tests/e2e_v1/` unless the stage explicitly touches engine/external boundaries) — full unit + integration suite
   This rule exists because CI lint failures on a freshly opened PR are pure self-inflicted noise — they burn review cycles and Greptile time. The CI workflow lives in `.github/workflows/`; mirror its commands locally.
4. **Push + open PR** — push the branch and open a PR via `gh pr create` against `main`. PR title: `Stage N — <stage name>`. PR body: stage goal, summary of changes, test status, with a checklist confirming the Step 3 local-CI checks all ran green.
5. **Wait 60s for Greptile** — Greptile is auto-subscribed; it posts review comments inline. After pushing, wait 60s, then fetch comments via `gh api repos/.../pulls/<N>/comments` and `gh pr view <N> --json reviews`.
6. **Resolve Greptile feedback** — for each comment, either (a) apply the fix in code and push, or (b) reply on the comment explaining why the suggestion does not apply. Push the resolution commits. No silent dismissals. Re-run the full Step 3 local-CI suite before each push.
7. **Wait for human merge** — poll merge status every 60s via `gh pr view <N> --json state,mergedAt`. No further changes, no further pings to the user. Just wait.
8. **On merge** — checkout `main`, pull, delete the local stage branch, advance to the next stage. Do not ask whether to proceed; the merge is the green light.

If the merge does not happen, polling continues. If a stage gets blocked (Greptile finds a real bug that's hard to fix, or the user pushes back on the merge with comments), surface the blocker and wait — but only after the standard 60s loops have run.

---

## Phase 1 — Engine v1 (complete)

Engine v1 (T1–T6) shipped in PR #30 (sha 888df55). The complete fix-bug workflow runs end-to-end against real Claude + real GitHub. 213 v1 unit tests pass. See `git log` and `project_hammock_t6.md` for details.

**Where the new engine lives:**
- `engine/v1/` — driver, dispatcher, loop_dispatch, predicate, resolver, hil, code_dispatch, artifact, substrate, prompt, validator, loader, git_ops.
- `shared/v1/types/` — job-request, bug-report, design-spec, impl-spec, impl-plan, review-verdict, pr, pr-merge-confirmation, summary, list_wrapper, registry, protocol.
- `tests/e2e_v1/` — T1–T6 workflow harness, gated on `HAMMOCK_E2E_REAL_CLAUDE=1`.

**What v0 still ships under the dashboard (slated for Phase 2 cleanup):**
- `job_driver/` — v0 stage executor. Unused by v1 jobs; deleted in Stage 5.
- `dashboard/state/cache.py` — in-memory job state. Deleted in Stage 3.
- `dashboard/hil/contract.py` — HIL submission validator. Deleted in Stage 3.
- `dashboard/mcp/{manager.py, server.py}` — per-stage MCP with four tools. Slimmed in Stage 4.
- `dashboard/frontend/src/` — Vue SPA built around v0 stage primitive. Rebuilt in Stage 6.

---

## Phase 2 — Dashboard + frontend cutover

Six stages. Each is independently shippable and protected by the harness landing in Stage 1.

| Stage | Goal | Touches | Depends on |
|---|---|---|---|
| **1. Backend integration harness** | Regression net + engine integration test surface | `tests/integration/`, `shared/paths.py` | — |
| **2. Type rework** | New / simplified variable types + `NodeContext.inputs` + `form_schema()` | `shared/v1/types/`, `engine/v1/hil.py`, T4/T5/T6 yamls, e2e stitcher | Stage 1 (harness verifies) |
| **3. Disk-first dashboard** | Delete cache + thin HIL handler. Disk is authoritative. | `dashboard/state/`, `dashboard/api/`, `dashboard/hil/`, `dashboard/app.py` | Stage 1 |
| **4. MCP slim** | Cut tool surface to `ask_human`, per-job spawn, node-scoped | `dashboard/mcp/`, agent spawn env | Stage 1 |
| **5. v0 cutover** | Delete `job_driver/`, compile-endpoint runs v1 validator, lifecycle spawns v1 driver | `dashboard/compiler/`, `dashboard/driver/lifecycle.py`, `job_driver/` (delete) | Stages 2–4 |
| **6. Frontend rebuild + UI tests** | Two-pane node-centric job page, JobsList, iteration URL, FormRenderer, Playwright | `dashboard/frontend/src/`, `tests/integration/ui/` | Stages 2–5 |

Order rationale (KISS/YAGNI):
- **Harness first** — every later stage is a destructive change. Without the net, regressions go silent.
- **Type rework before backend cleanup** — engine-only change. Doesn't depend on dashboard changes; verifies T1–T6 still pass under the new types before we start ripping the dashboard apart.
- **Backend cleanup before frontend** — frontend rewrite needs a stable backend contract to build against. Cache delete + thin HIL + slim MCP + v0 cutover give it that.
- **Frontend last** — biggest piece, depends on all backend cleanup being done. Bundles its own Playwright tests; no point speccing UI tests for a frontend that's going to be torn out anyway.

---

# Stage 1 — Backend integration harness

## 1.1 Goal

Build the offline test surface that protects every Stage 2–6 change. Disk is the only contract between engine and dashboard; this stage gives us a way to script disk state, observe a live dashboard against it, and assert REST + SSE + HIL POST behave correctly. Plus a focused MCP-server roundtrip test.

No real driver process. No real Claude / `gh` / `git`. Real-API coverage stays in `tests/e2e_v1/test_workflow.py` (gated on `HAMMOCK_E2E_REAL_CLAUDE=1`, run pre-merge — not the development loop).

## 1.2 Pieces

| Piece | Role |
|---|---|
| **`FakeEngine`** | Python helper. Knows the v1 disk layout. Scripts disk state by writing files via `shared.atomic.*` — same primitives the real engine uses, so resulting disk state is byte-identical. |
| **Live dashboard fixture** | Boots `create_app(Settings(root=tmp_path, run_background_tasks=True))` so the real watcher + supervisor + MCP manager run. Exposes async `httpx.AsyncClient(transport=ASGITransport(app))` for REST/SSE. Binds a localhost port for later (Playwright in Stage 6). |
| **MCP roundtrip test** | Standalone subprocess test of `dashboard/mcp/server.py`. Sends an `ask_human` tool call over stdio, asserts the pending marker appears, simulates the dashboard POST having succeeded, asserts the MCP server returns the answer. |

## 1.3 Layout

```
tests/integration/
  conftest.py                       # FakeEngine fixture, live dashboard fixture
  fake_engine.py                    # disk-side scripting helper
  dashboard/
    test_disk_contract.py           # watcher classifies every v1 path
    test_projections.py             # cache projection produces expected API shape
    test_sse_replay_live.py         # replay + live transition + Last-Event-ID
    test_hil_path_a.py              # explicit HIL — pending → POST → answered
    test_hil_path_b_dashboard.py    # implicit HIL (dashboard side)
    test_loop_unroll.py             # iteration paths project to indented node list
    test_skipped_node.py            # runs_if-skipped node renders SKIPPED
  mcp/
    test_ask_human_roundtrip.py     # MCP server alone, subprocess + stdio
```

## 1.4 `FakeEngine` API (Step-0 stub)

```python
# tests/integration/fake_engine.py
from pathlib import Path
from typing import Any
from pydantic import BaseModel
from shared.models.job import JobState

class FakeEngine:
    """Disk-side scripting for the v1 layout. No driver process.

    Every method writes via shared.atomic.* — same primitives the real
    engine uses. Resulting disk state is byte-identical to a real run.
    """

    def __init__(self, root: Path, job_slug: str, *, project_slug: str = "test-project") -> None: ...

    # Job lifecycle
    def start_job(self, *, workflow: dict[str, Any], request: str) -> None: ...
    def finish_job(self, state: JobState) -> None: ...

    # Node lifecycle (iter=() top-level, (n,) one loop, (n,m) nested)
    def enter_node(self, node_id: str, *, iter: tuple[int, ...] = ()) -> None: ...
    def complete_node(self, node_id: str, value: BaseModel, *, iter: tuple[int, ...] = ()) -> None: ...
    def fail_node(self, node_id: str, error: str, *, iter: tuple[int, ...] = ()) -> None: ...
    def skip_node(self, node_id: str, reason: str, *, iter: tuple[int, ...] = ()) -> None: ...

    # Stream side
    def emit_log(self, node_id: str, line: str, *, iter: tuple[int, ...] = ()) -> None: ...
    def emit_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        node_id: str | None = None,
        iter: tuple[int, ...] = (),
    ) -> None: ...

    # HIL
    def request_hil(
        self,
        node_id: str,
        type_name: str,
        *,
        iter: tuple[int, ...] = (),
        prompt: str | None = None,
        output_var_names: list[str] | None = None,
    ) -> str:
        """Drops a pending marker at pending/<node_id>.json. Returns the
        node_id (used as the gate identifier by dashboard + stitcher)."""
        ...

    def assert_hil_answered(self, node_id: str, *, iter: tuple[int, ...] = ()) -> BaseModel:
        """Verify the HIL gate has been answered: pending marker is gone
        AND the corresponding variable envelope (loop-indexed when iter
        is non-empty) exists. Returns the parsed Value. Raises if either
        precondition fails. There is no separate hil/answered/ — the
        envelope's existence IS the answer."""
        ...
```

All paths come from `shared/paths.py`. `FakeEngine` does not encode path strings inline — single source of truth.

## 1.5 Live dashboard fixture (Step-0 stub)

```python
# tests/integration/conftest.py
@dataclass
class DashboardHandle:
    client: httpx.AsyncClient        # for REST + SSE assertions
    url: str                         # localhost:<port> (uvicorn), for Playwright in Stage 6
    root: Path                       # passed to FakeEngine

@pytest.fixture
async def dashboard(tmp_path: Path) -> AsyncIterator[DashboardHandle]:
    """Boots a live dashboard against tmp_path. Watcher runs.
    Cleanup cancels lifespan tasks and shuts down uvicorn cleanly."""
    raise NotImplementedError

@pytest.fixture
async def fake_engine(dashboard: DashboardHandle) -> FakeEngine:
    """A FakeEngine bound to the dashboard's root and a fresh job slug."""
    raise NotImplementedError
```

Critical detail: `run_background_tasks=True` (the existing `populated_root` fixture sets it `False` to avoid races; integration tests *want* the watcher running). Test code waits on disk-state propagation via short polling on the dashboard's API or SSE stream — never `time.sleep` for fixed durations.

## 1.6 Test surface

Stage 1 ships the harness itself (FakeEngine + live dashboard fixture). The dashboard suites in §1.6 below are *spec stubs* — descriptive test names + `NotImplementedError` bodies — that later stages fill in as those stages enable the corresponding behavior. This keeps Stage 1 KISS while leaving the spec visible from day one.

| Suite | Owned by | What it asserts |
|---|---|---|
| `test_harness.py` | **Stage 1** (concrete tests) | FakeEngine writes the right files at the right paths via `shared.atomic.*`. Dashboard fixture starts cleanly with the watcher running, binds a localhost port, exposes a working httpx async client, and shuts down without leaks. End-to-end smoke: `fake_engine.start_job(...)` followed by `dashboard.client.get("/api/health")` returns 200. |
| `test_disk_contract.py` | **Stage 3** (disk-first dashboard) | Writing each v1 path via `FakeEngine` is observable to the dashboard. Asserts on `GET /api/jobs/:slug`, `GET /api/jobs/:slug/nodes/:id` and similar — drives Stage 3's disk-first projection rewrite. |
| `test_projections.py` | **Stage 3** | API JSON shape including loop iterations unrolled, SKIPPED nodes, resolved envelopes. |
| `test_sse_replay_live.py` | **Stage 3** | SSE replay + `Last-Event-ID` continuity + scope filters. |
| `test_hil_path_a.py` | **Stage 3** | Explicit HIL round-trip through dashboard's thin handler. |
| `test_hil_path_b_dashboard.py` | **Stage 3** | Dashboard-side of MCP-initiated HIL (same handler). |
| `test_loop_unroll.py` | **Stage 3** | 3-iteration outer + 2-iteration inner unrolls correctly in the API. |
| `test_skipped_node.py` | **Stage 3** | `runs_if`-skipped nodes render SKIPPED. |
| `test_ask_human_roundtrip.py` | **Stage 4** (MCP slim) | MCP server subprocess writes pending marker + reads answer. |

This means Stage 1's Step 1 deliverable is `test_harness.py` (concrete failing tests). The remaining suites are committed as Step-0 stubs with `NotImplementedError` so they're discoverable but don't fail the Stage 1 DoD. Each later stage's Step 1 = filling in its owned suite.

## 1.7 Order of work

**Step 0 — interfaces.** Stub `FakeEngine`, `DashboardHandle`, `dashboard` + `fake_engine` fixtures, MCP roundtrip test scaffolding. Every method `NotImplementedError`. `mypy` clean. One commit.

**Step 1 — failing tests.** Write all suites in §1.6 against the Step-0 stubs. One PR (or split per suite if it helps review). Frozen for Step 3.

**Step 2 — implementation, in dependency order:**

1. `shared/paths.py` v1 helpers (loop iteration paths, hil paths, vars paths, logs paths). Unit tests verify each helper produces the expected path.
2. `FakeEngine` job + node lifecycle methods. Unit tests verify each writes the right file at the right path with the right content.
3. `FakeEngine` stream + HIL methods. Unit tests verify pending/answered shape, event JSONL append.
4. Live dashboard fixture. Unit-equivalent: a smoke test that the fixture starts and stops cleanly, watcher runs, port binds.
5. MCP roundtrip test infrastructure (subprocess management, stdio framing helpers).

**Step 3 — fix loop.** Run the §1.6 suites. Fix the implementation until green. §1.6 tests are frozen — surface anything that needs to change in them.

## 1.8 Definition of done

- `tests/integration/test_harness.py` green on a single run, no flakes — proves FakeEngine + dashboard fixture work.
- All other suites (`test_disk_contract.py`, `test_projections.py`, etc.) collect under pytest with `NotImplementedError` bodies. They are the spec for later stages.
- `tests/integration/` runs in <60s on a laptop, no network, no API keys.
- `tests/e2e_v1/test_workflow.py` (real Claude + real GitHub) unchanged and still green pre-merge.
- A `scripts/play-fake-job.py <scenario>` CLI that drives the dev-server dashboard from a `FakeEngine` script — for visual debugging during Stage 6.

---

# Stage 2 — Type rework

## 2.1 Goal

Ship the new variable type shapes the rest of the cutover depends on. Engine-only change. T1–T6 must still pass after this stage.

## 2.2 Decisions

### review-verdict simplifies

`shared/v1/types/review_verdict.py` drops the `Concern` sub-model and the `unresolved_concerns` / `addressed_in_this_iteration` fields. Final shape:

```python
class ReviewVerdictValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["approved", "needs-revision", "rejected"]
    summary: str = Field(..., min_length=1)
```

`form_schema()` returns `FormSchema(fields=[("verdict", "select:approved,needs-revision,rejected"), ("summary", "textarea")])`.

### pr-merge-confirmation deletes; pr-review-verdict added

`shared/v1/types/pr_merge_confirmation.py` deletes. `shared/v1/types/pr_review_verdict.py` added.

```python
class PRReviewVerdictValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["merged", "needs-revision"]
    summary: str  # engine-populated, NOT human-typed
```

`form_schema()` returns `FormSchema(fields=[("verdict", "select:merged,needs-revision")])`. Two buttons, no textarea.

`produce` behaviour:
- Human submits `{verdict: "merged" | "needs-revision"}`. That is the entire submission payload.
- Engine reads upstream `pr` from `ctx.inputs["pr"]` → `pr.url`.
- If `verdict == "merged"`: `gh pr view <url> --json state` → reject submission if state ≠ MERGED. Set `value.summary = ""` (or short confirmation).
- If `verdict == "needs-revision"`: `gh pr view <url> --json comments,reviews,statusCheckRollup` → format into structured prose (reviewer feedback, inline comments, failing checks). Set `value.summary` to that prose.
- Envelope lands at `loop_pr-merged-loop_pr_review_<i>.json`. Next implement iteration reads `$pr-merged-loop.pr_review[i-1]?.summary` → aggregated GitHub feedback in one blob.

GitHub becomes the single surface for review activity. Hammock pulls from `gh` on submission and gives the agent everything in one prose summary.

### NodeContext extension

`shared/v1/types/protocol.py` extends `NodeContext` with `inputs: dict[str, Any]` so human-actor `produce` methods can read upstream variable values. `engine/v1/hil.submit_hil_answer` resolves the node's declared inputs and populates this map before invoking `produce`.

### `form_schema()` added to `VariableType` protocol

`shared/v1/types/protocol.py` adds:

```python
class FormSchema(BaseModel):
    fields: list[tuple[str, str]]  # (field_name, widget_type) — widget_type strings:
                                    # "select:opt1,opt2,...", "textarea", "text", ...

class VariableType(Protocol):
    ...
    @classmethod
    def form_schema(cls) -> FormSchema | None:
        """For human-input types, return the form schema. Agent-only
        types return None."""
```

Implementations:
- `review-verdict`, `pr-review-verdict` — return a `FormSchema`.
- All other types (`bug-report`, `design-spec`, `impl-spec`, `impl-plan`, `summary`, `pr`, `branch`, `job-request`) — return `None`.

### Workflow YAML adjustments

T4 / T5 / T6 yamls swap `pr_merge: { type: pr-merge-confirmation }` → `pr_review: { type: pr-review-verdict }`. Loop predicate becomes:

```yaml
until: $pr-merged-loop.pr_review[i].verdict == 'merged'
```

### e2e_v1 stitcher policy

`tests/e2e_v1/hil_stitcher.py` policy `merge_pr_then_confirm` simplifies to submitting `{verdict: "merged"}` after `gh pr merge --squash --admin` succeeds.

## 2.3 Concrete file changes

| File | Change |
|---|---|
| `shared/v1/types/pr_merge_confirmation.py` | DELETE |
| `shared/v1/types/pr_review_verdict.py` | NEW |
| `shared/v1/types/review_verdict.py` | EDIT — drop Concern + unresolved_concerns + addressed_in_this_iteration |
| `shared/v1/types/protocol.py` | EDIT — add `FormSchema`, add `form_schema()` to protocol, add `inputs: dict[str, Any]` to `NodeContext` |
| `shared/v1/types/registry.py` | EDIT — drop pr-merge-confirmation, add pr-review-verdict |
| `shared/v1/types/{bug_report,design_spec,impl_spec,impl_plan,summary,pr,branch,job_request,list_wrapper}.py` | EDIT — add `form_schema(cls) -> None` to each |
| `engine/v1/hil.py` | EDIT — `submit_hil_answer` resolves declared inputs and populates `NodeContext.inputs` before invoking `produce` |
| `tests/e2e_v1/workflows/T4.yaml` `T5.yaml` `T6.yaml` | EDIT — swap pr-merge-confirmation → pr-review-verdict, update loop predicate |
| `tests/e2e_v1/hil_stitcher.py` | EDIT — `merge_pr_then_confirm` submits `{verdict: "merged"}` |
| `tests/shared/v1/types/test_pr_merge_confirmation.py` | DELETE |
| `tests/shared/v1/types/test_pr_review_verdict.py` | NEW (Step-2 unit tests) |
| `tests/shared/v1/types/test_review_verdict.py` | EDIT — assertions on simplified shape |

## 2.4 Order of work

**Step 0 — interfaces.** Stub `pr_review_verdict.py` (Decl + Value + class skeleton with `produce` raising NotImplementedError). Edit `protocol.py` to add `FormSchema` + `form_schema` + `NodeContext.inputs`. Edit `review_verdict.py` to the simplified shape with `produce` raising. `mypy` clean.

**Step 1 — failing tests.**
- New unit tests on the new shapes (will fail with NotImplementedError or assertion).
- Extend `tests/integration/dashboard/test_hil_path_a.py` to cover `pr-review-verdict` round-trip end-to-end.
- Run T4/T5/T6 e2e against real Claude with the new yamls — they will fail because `produce` is unimplemented.

**Step 2 — implementation, in dependency order:**

1. `protocol.py`: `FormSchema`, `form_schema` protocol method, `NodeContext.inputs`. Unit tests on the BaseModel shapes.
2. `review_verdict.py`: drop fields, simplify `produce`, add `form_schema`. Unit tests on accepted/rejected payloads.
3. `pr_review_verdict.py`: implement `produce` (gh pr view branch by verdict; on merged → state check; on needs-revision → fetch and format comments/reviews/checks). Unit tests with a mocked `gh` callable.
4. All other types: trivial `form_schema -> None` addition. Unit tests verify each returns None.
5. `engine/v1/hil.py`: extend `submit_hil_answer` to resolve declared inputs and populate `NodeContext.inputs`. Unit tests verify resolution + population.
6. `registry.py`: registration swap. Unit test verifies registry contents.
7. T4/T5/T6 YAMLs + stitcher policy.

**Step 3 — fix loop.** Run §2.4 Step 1 tests + integration suite + e2e_v1 (real Claude). Fix until green.

## 2.5 Definition of done

- All Stage 1 integration tests still green.
- New `pr-review-verdict` integration test in `test_hil_path_a.py` green.
- T4/T5/T6 e2e (`HAMMOCK_E2E_REAL_CLAUDE=1`) green.
- All v1 unit tests green.
- `pr-merge-confirmation` references gone from the codebase.

---

# Stage 3 — Disk-first dashboard

## 3.1 Goal

Delete `dashboard/state/cache.py`. Delete `dashboard/hil/contract.py`. Every dashboard handler reads disk directly. HIL POST becomes a thin wrapper over `engine/v1/hil.submit_hil_answer`.

## 3.2 Decisions

### Cache deletes entirely

- `dashboard/state/cache.py` removes.
- Every dashboard HTTP handler reads disk directly (job dir layout per `shared/paths.py`).
- `dashboard/state/projections.py` becomes pure functions of `(job_dir → response payload)`. No in-memory state.
- `dashboard/state/pubsub.py` stays as the SSE fan-out primitive; the watcher tails `events.jsonl` and pushes lines to subscribers.
- Caching can come back if a real workload demands it. None does today.

### `dashboard/hil/contract.py` deletes

`engine/v1/hil.submit_hil_answer` already covers what the contract layer did:
- Validates the typed payload via the variable type's `produce`.
- Writes the envelope to the correct path (loop-indexed when applicable).
- Removes the pending marker atomically.
- Raises `HilSubmissionError` with a human-readable message on failure.

`dashboard/api/hil.py` becomes a thin FastAPI handler (~30 lines): parses the POST body, calls `engine/v1/hil.submit_hil_answer`, translates `HilSubmissionError` → HTTP 400 with the error message. SSE event emission stays implicit — the engine writes `events.jsonl` as part of `submit_hil_answer`'s atomic step; the watcher tails and fans out to subscribers.

### Watcher unchanged

`dashboard/watcher/tailer.py` stays as-is. It already classifies paths and emits `CacheChange` events. Without the cache, subscribers consume those events directly (mainly the SSE handler).

## 3.3 Concrete file changes

| File | Change |
|---|---|
| `dashboard/state/cache.py` | DELETE |
| `dashboard/hil/contract.py` | DELETE |
| `dashboard/state/projections.py` | EDIT — pure functions over `(job_dir, ...) -> dict`. No state. |
| `dashboard/api/hil.py` | EDIT — thin wrapper over `engine.v1.hil.submit_hil_answer` |
| `dashboard/api/jobs.py` | EDIT — read disk directly via projections |
| `dashboard/api/stages.py` | EDIT — same; rename internally to nodes if convenient (or defer to Stage 6) |
| `dashboard/api/sse.py` | EDIT — subscriber consumes `CacheChange` from pubsub directly; no cache hop |
| `dashboard/api/artifacts.py` | EDIT — read disk directly |
| `dashboard/api/observatory.py` `costs.py` `settings.py` `projects.py` `stage_actions.py` `job_submit.py` | EDIT — remove cache lookups; read disk directly |
| `dashboard/app.py` | EDIT — lifespan no longer bootstraps cache; pubsub still created |
| `tests/dashboard/state/test_cache.py` | DELETE |
| `tests/dashboard/hil/test_contract.py` | DELETE |
| `tests/dashboard/state/test_projections.py` | EDIT — exercise pure-function shape |
| `tests/dashboard/api/test_hil_post.py` | EDIT — thin handler covers the same surface |
| `tests/dashboard/api/test_*.py` | EDIT — anywhere that pre-populated cache must now pre-populate disk via `populated_root` (already disk-shaped) |
| `tests/dashboard/conftest.py` | EDIT — `populated_root` already builds disk state; remove any cache pre-population if present |

## 3.4 Order of work

**Step 0 — interfaces.** New signatures for projection pure functions in `dashboard/state/projections.py`. New skeleton for thin `dashboard/api/hil.py`. `mypy` clean.

**Step 1 — failing tests.**
- Stage 1 integration tests (`test_disk_contract.py`, `test_projections.py`, `test_sse_replay_live.py`, `test_hil_path_a.py`, `test_hil_path_b_dashboard.py`, `test_loop_unroll.py`, `test_skipped_node.py`) become Step 1 for this stage. They were green at end of Stage 1. They will go red after Stage 3 starts (because cache removal breaks every handler). They drive Stage 3 back to green.
- Add new tests in the same suites for any disk-first behaviour the existing suites don't already cover (e.g., a handler that previously had a fast-path through cache now needs an assertion that the disk read is correct under load).

**Step 2 — implementation, in dependency order:**

1. `dashboard/state/projections.py` — rewrite as pure functions. Unit tests over fabricated `job_dir` paths.
2. `dashboard/api/sse.py` — subscriber consumes pubsub directly; no cache hop. Unit tests on the subscriber loop.
3. Each `dashboard/api/<route>.py` in turn — read disk via projections. Re-run unit tests.
4. `dashboard/api/hil.py` — thin handler. Unit tests verify error translation + SSE emission (via `submit_hil_answer`).
5. `dashboard/app.py` lifespan — drop cache bootstrap. Unit tests on app startup.
6. Delete `cache.py` and `hil/contract.py`. Re-run all unit tests.

**Step 3 — fix loop.** Run Stage 1 integration suites + the new disk-first additions. Fix until green.

## 3.5 Definition of done

- `dashboard/state/cache.py` and `dashboard/hil/contract.py` no longer exist.
- All Stage 1 integration tests green.
- All dashboard unit tests green.
- T1–T6 e2e (`HAMMOCK_E2E_REAL_CLAUDE=1`) green.
- `dashboard/app.py` lifespan no longer references the cache.

---

# Stage 4 — MCP slim

## 4.1 Goal

Cut the MCP tool surface to one tool (`ask_human`). One MCP server per job (was per stage). Agents inherit a node-scoping env var.

## 4.2 Decisions

### Tool inventory

| v0 tool | v1 verdict |
|---|---|
| `open_ask` | RENAME → `ask_human(question) -> answer`. Writes a node-scoped pending marker via `engine/v1/hil.write_pending_marker`, waits for the human submission, returns the answer string. |
| `append_stages` | DROP — no dynamic stage-list mutation in v1 (static DAG). |
| `open_task` | DROP — agent prints to stdout if a sub-task is worth surfacing; UI shows it in the stream pane. |
| `update_task` | DROP — same reason. |

Net surface: one tool, down from four. Re-add `open_task` / `update_task` only when their absence proves painful.

### Per-job spawn

- One MCP server process per job. Spawned at job submit / driver bootstrap; torn down on terminal state.
- `dashboard/mcp/manager.py`: spawn moves from per-stage to per-job.

### Node scoping via env var

- Each agent subprocess inherits the MCP socket env var **plus** `HAMMOCK_NODE_ID` and (when inside a loop body) `HAMMOCK_NODE_ITER` (string `"0"`, `"1,0"`, etc.) so the server can scope tool calls to the calling node.
- `dashboard/mcp/server.py` reads these env vars per-tool-call to scope the pending marker correctly.

### Implicit HIL shape

Implicit HIL (agent-initiated `ask_human` via MCP) keeps a separate, fixed shape: `{question}` in, `{answer}` out. Single dedicated component on the frontend (Stage 6), no schema dispatch.

## 4.3 Concrete file changes

| File | Change |
|---|---|
| `dashboard/mcp/server.py` | EDIT — remove `append_stages`, `open_task`, `update_task` tools; rename `open_ask` → `ask_human`; scope by `HAMMOCK_NODE_ID` + `HAMMOCK_NODE_ITER` env. |
| `dashboard/mcp/manager.py` | EDIT — per-job spawn (remove per-stage spawn loop). |
| `dashboard/mcp/channel.py` | EDIT — adapt if channel handshake referenced the dropped tools. |
| `engine/v1/dispatcher.py` (or wherever agents are spawned) | EDIT — pass `HAMMOCK_NODE_ID` + `HAMMOCK_NODE_ITER` in subprocess env. |
| `tests/dashboard/mcp/test_server.py` | EDIT — drop tests for removed tools, add tests for `ask_human` rename + scoping. |
| `tests/dashboard/mcp/test_manager.py` | EDIT — assert per-job spawn lifecycle. |
| `tests/integration/mcp/test_ask_human_roundtrip.py` (Stage 1) | EDIT — assert env-var scoping populates the right pending path. |

## 4.4 Order of work

**Step 0 — interfaces.** Stub the new `ask_human` tool signature. Stub the per-job manager method. `mypy` clean.

**Step 1 — failing tests.** The Stage 1 MCP roundtrip test grows to assert env-var scoping. Manager unit tests rewritten for per-job lifecycle. They fail.

**Step 2 — implementation:**

1. `dashboard/mcp/server.py` — rename + drop + scope. Unit tests verify each tool call writes a node-scoped pending marker.
2. `dashboard/mcp/manager.py` — per-job spawn lifecycle. Unit tests verify spawn-on-submit, teardown-on-terminal.
3. `engine/v1/dispatcher.py` — pass env vars to spawned agents. Unit tests verify env var presence.
4. Run all dashboard MCP unit tests. Run Stage 1 MCP roundtrip integration test.

**Step 3 — fix loop.** Run all MCP tests + integration roundtrip + T1–T6 e2e. Fix until green.

## 4.5 Definition of done

- MCP server exposes only `ask_human`.
- One MCP process per job in lifecycle.
- Stage 1 MCP roundtrip test green with env-var scoping.
- T1–T6 e2e green.

---

# Stage 5 — v0 cutover

## 5.1 Goal

Delete v0 entirely. Compile endpoint runs `engine/v1/validator`. Lifecycle spawns `python -m engine.v1.driver`.

## 5.2 Decisions

- The v0 engine (`job_driver/`) deletes after cutover. No backwards compatibility, no parallel v0+v1 driver, no version detection at the compile endpoint.
- The frontend ships a single Vue build that talks only to v1 (frontend rewrite is Stage 6; this stage just removes v0 paths in the backend).
- Compile endpoint (`dashboard/compiler/compile.py`) replaces v0 logic with `engine/v1/validator.assert_valid` on every submitted YAML. v0 syntax is rejected.
- `dashboard/driver/lifecycle.py` adapts to spawn `python -m engine.v1.driver`.
- `dashboard/specialist/` retires with v0 (was v0 plan-compiler logic).

## 5.3 Concrete file changes

| File | Change |
|---|---|
| `job_driver/` (entire directory) | DELETE |
| `dashboard/compiler/compile.py` | EDIT — call `engine.v1.validator.assert_valid`; reject v0 syntax. |
| `dashboard/compiler/validators.py` `materialise.py` | EDIT or DELETE depending on what's still meaningful under v1; default to DELETE. |
| `dashboard/specialist/` | DELETE (v0 plan-compiler) |
| `dashboard/driver/lifecycle.py` | EDIT — spawn `python -m engine.v1.driver`. |
| `dashboard/driver/supervisor.py` | EDIT — supervisor logic remains the same; just spawns v1 driver. |
| `cli/job.py` | EDIT — remove any v0-specific subcommand surface. |
| `tests/job_driver/` | DELETE |
| `tests/dashboard/specialist/` | DELETE |
| `tests/dashboard/compiler/test_compile.py` | EDIT — assert v1 validator runs; v0 syntax rejected. |
| `tests/dashboard/driver/test_lifecycle.py` | EDIT — assert v1 driver spawn. |

## 5.4 Order of work

**Step 0 — interfaces.** Stub the new `compile.py` signature (calls v1 validator). Stub the new `lifecycle.py` spawn signature. `mypy` clean — note the deletes will break imports; spend Step 0 fixing those.

**Step 1 — failing tests.**
- Rewritten compile test asserts v1 validator runs and rejects a v0-shaped YAML.
- Rewritten lifecycle test asserts the spawned process is `python -m engine.v1.driver`.
- A new integration test (in `tests/integration/dashboard/`) drives a real job submit + driver spawn against the live dashboard fixture using a small v1 YAML. Assert the driver reaches a terminal state.

**Step 2 — implementation:**

1. `dashboard/compiler/compile.py` — call v1 validator. Unit tests on accepted/rejected YAMLs.
2. `dashboard/driver/lifecycle.py` — spawn v1 driver. Unit tests verify spawn args + env.
3. Delete `job_driver/`, `dashboard/specialist/`, `dashboard/compiler/{validators,materialise}.py`. Fix every import error that surfaces (treat each as a Step-2 sub-component: write a small unit test where it makes sense, then fix).
4. Re-run all unit tests.

**Step 3 — fix loop.** Run integration suites + T1–T6 e2e. Fix until green.

## 5.5 Definition of done

- `job_driver/` and `dashboard/specialist/` no longer exist.
- Compile endpoint accepts only v1 YAMLs.
- Lifecycle spawns `python -m engine.v1.driver`.
- T1–T6 e2e green via the dashboard's spawn path (not just direct driver invocation).
- Stage 1 integration suites green.

---

# Stage 6 — Frontend rebuild + UI tests

## 6.1 Goal

Replace the v0 stage-detail surface with a node-centric two-pane job page. Add a JobsList view. Add `FormRenderer` + widget map for HIL forms (replacing `template_registry`). Add Playwright UI tests.

## 6.2 Decisions

### Sidebar + jobs list

- Sidebar gains a `Jobs` entry between `Projects` and `HIL`.
- New route `/jobs` → jobs list page. Rows: `slug, state, cost, duration`. Click → job page.

### Job page (`/jobs/:slug`)

Two-pane layout:

**Left pane — node list.** Workflow declaration order, with loops unrolled inline.
- Top-level nodes appear as flat rows.
- Loop nodes are not shown as a single row. Their iterations are unrolled and each iteration's body nodes appear under a per-iteration section header (`iter 0:`, `iter 1:`, …), indented one level.
- Nested loops recurse the same pattern: deeper indentation per nesting level.
- For `until` loops, iteration sections appear lazily — iter 0 visible while running; iter 1 appears when the predicate fails and the body runs again.

**Right pane — stream view.** Two modes:
- **Default** (no node selected): job-wide common stream — `events.jsonl` lifecycle events interleaved with per-node stdout/stderr in chronological order.
- **Node selected**: detail for that node-execution. Contents:
  - State badge (RUNNING / SUCCEEDED / SKIPPED / FAILED).
  - For `actor: agent`: prompt + stdout stream + `result.json`.
  - For `kind: code`: the above plus worktree path, stage branch name, opened-PR link.
  - For `actor: human`: pending form (if open) or submitted answer (if past).
  - Resolved inputs (rendered via the variable types' `render_for_consumer`) + produced outputs (envelope JSON, prettified).

### URL scheme — iteration identity

```
/jobs/:slug                                    # default — no node selected
/jobs/:slug?node=write-bug-report              # top-level node, no iteration
/jobs/:slug?node=implement&iter=0              # body node inside one loop
/jobs/:slug?node=implement&iter=0,0            # body node inside nested loops
                                               # iter list = (outer-iter, inner-iter, ...)
```

### Routing churn

| Today | Replacement |
|---|---|
| `/jobs/:jobSlug` (`JobOverview.vue`) | rebuild as the two-pane page. |
| `/jobs/:jobSlug/stages/:stageId` (`StageLive.vue`) | DELETE — node detail collapses into the right pane of `/jobs/:slug?node=…`. |
| (no `/jobs` listing today) | NEW `/jobs` route + `JobsList.vue`. |

### Component churn

- `components/stage/*` REPLACE → becomes `components/node/*`. Internal pieces (stream pane, state badge, budget bar) survive shape; orchestration changes.
- `views/StageLive.vue` DELETE.
- `views/JobOverview.vue` rewrite for two-pane layout.
- `views/JobsList.vue` NEW.
- Router updated; old stage route removed.

### HIL form rendering — FormRenderer + widget map

- One generic `FormRenderer.vue`.
- `Map<widget_type_string, VueComponent>` widget map. Widget type strings come from the backend `FormSchema` (Stage 2): `"select:opt1,opt2,..."`, `"textarea"`, `"text"`, etc.
- Adding a new variable type that needs a custom widget = one entry in the widget map plus one ~30-line Vue component.
- `template_registry.py` (per-stage-kind dispatcher) RETIRES. Per-variable-type dispatch via `FormSchema` replaces it.
- Implicit HIL (`ask_human` via MCP) keeps a separate fixed-shape component: `{question}` in, `{answer}` out. No schema dispatch.

### Playwright tests

Live dashboard fixture from Stage 1 already binds a localhost port. Stage 6 adds Playwright config + tests.

## 6.3 Concrete file changes

| File | Change |
|---|---|
| `dashboard/frontend/src/views/JobsList.vue` | NEW |
| `dashboard/frontend/src/views/JobOverview.vue` | REWRITE — two-pane layout |
| `dashboard/frontend/src/views/StageLive.vue` | DELETE |
| `dashboard/frontend/src/components/stage/*` | RENAME → `components/node/*` and reshape orchestration |
| `dashboard/frontend/src/components/hil/FormRenderer.vue` | NEW |
| `dashboard/frontend/src/components/hil/widgets/Select.vue` | NEW |
| `dashboard/frontend/src/components/hil/widgets/Textarea.vue` | NEW |
| `dashboard/frontend/src/components/hil/widgets/Text.vue` | NEW (placeholder; add only if a type needs it) |
| `dashboard/frontend/src/components/hil/AskHumanDisplay.vue` | NEW (fixed-shape implicit-HIL component) |
| `dashboard/frontend/src/components/hil/template_registry.ts` | DELETE |
| `dashboard/frontend/src/router.ts` | EDIT — delete `/jobs/:jobSlug/stages/:stageId`, add `/jobs` |
| `dashboard/frontend/src/sidebar.ts` (or equivalent) | EDIT — add Jobs entry |
| `dashboard/frontend/src/queries.ts` `client.ts` | EDIT — adapt to Stage 3 disk-first API shapes |
| `dashboard/frontend/src/sse.ts` | EDIT — adapt to URL-scheme iteration scoping |
| `tests/integration/ui/playwright.config.ts` | NEW |
| `tests/integration/ui/test_jobs_list.spec.ts` | NEW |
| `tests/integration/ui/test_two_pane_job_page.spec.ts` | NEW |
| `tests/integration/ui/test_iteration_drilldown.spec.ts` | NEW |
| `tests/integration/ui/test_hil_form_submit.spec.ts` | NEW |

## 6.4 Order of work

**Step 0 — interfaces.** Vue component skeletons with declared props/events/slots typed, no implementation. New router config. New widget map registration shape. TypeScript clean (`tsc --noEmit`).

**Step 1 — failing Playwright tests.**
- `test_jobs_list.spec.ts`: navigate to `/jobs`, assert rows render, click navigates to `/jobs/:slug`.
- `test_two_pane_job_page.spec.ts`: with a `FakeEngine`-scripted job, assert left-pane node list shape, click switches right pane, default mode shows interleaved stream.
- `test_iteration_drilldown.spec.ts`: with a 3-iteration loop scripted, assert iter sections appear, URL preserves on reload, nested-loop iter list works.
- `test_hil_form_submit.spec.ts`: pending HIL for `pr-review-verdict`, assert two buttons render, click "Merged" submits the right payload.

**Step 2 — implementation, in dependency order:**

1. Router updates. Smoke unit test that routes resolve.
2. `JobsList.vue`. Vitest component test (or just integration via Playwright Step 1).
3. `JobOverview.vue` two-pane shell + left node list. Component tests on the unrolling logic.
4. Right pane default (common stream) + per-node detail. Component tests for each actor/kind variant.
5. `FormRenderer.vue` + widget map. Component tests for each widget.
6. `AskHumanDisplay.vue` for implicit HIL.
7. Iteration URL scheme — query param parser + state binding. Component test on URL ↔ state.
8. Sidebar `Jobs` entry.
9. Delete `StageLive.vue` + `components/stage/*` + `template_registry.ts`. Re-run all tests.

**Step 3 — fix loop.** Run Playwright suite. Fix until green. Manual click-test through one full T6 dogfood run.

## 6.5 Definition of done

- Playwright suite green, headless, <30s.
- Stage 1 integration suites still green.
- T1–T6 e2e green.
- Manual click-test through one full T6 run: no console errors, no broken routes, HIL submits work, loop iterations render, deep links preserve on reload.
- `StageLive.vue` and `components/stage/*` and `template_registry.ts` no longer exist.

---

## Out of scope (deferred)

These were considered during the cutover design and explicitly deferred. Not in any stage above; tackle in a future round once the v1 base lands.

- **Per-node chat (agent-node detail page only).** While an agent is running, the human types into a chat panel to interject. Requires either claude conversation/streaming mode rather than `-p`, or a polling mechanism where the agent checks for queued messages between tool calls. Significant change to how the engine spawns agents.
- **Job-level chat with a permanent "job assistant" agent.** A long-lived agent for the job's lifetime, reads job dir + events.jsonl + state, answers human questions, can relay to a currently-running node-agent. Distinct from any node in the workflow.
- **Caching.** `dashboard/state/cache.py` was deleted in Stage 3. If a real workload demands it, design and add — not before.
- **Per-stage / per-component MCP tools** (`open_task`, `update_task`, `append_stages`). Dropped in Stage 4. Re-add when their absence proves painful.
- **Vue component-level unit-test framework.** Stage 6 ships Playwright integration tests. Component-level unit tests grow if any single component becomes complex enough to warrant them.

---

## Status

| Phase | Status |
|---|---|
| Phase 1 — Engine v1 (T1–T6) | ✓ Complete (PR #30) |
| Stage 1 — Backend integration harness | Not started |
| Stage 2 — Type rework | Not started |
| Stage 3 — Disk-first dashboard | Not started |
| Stage 4 — MCP slim | Not started |
| Stage 5 — v0 cutover | Not started |
| Stage 6 — Frontend rebuild + UI tests | Not started |

---

(This document is the work plan. Updates to it are commits, not redrafts.)
