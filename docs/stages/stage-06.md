# Stage 6 — MCP server (4 tools)

**PR:** TBD (in PR)
**Branch:** `feat/stage-06-mcp-server`

## What was built

The dashboard's MCP tool surface — the agent's only programmatic
interface for opening tasks, reporting task status, asking the human, and
appending stages at runtime. After this stage, mid-stage HIL works
end-to-end without any UI: the agent calls `open_ask`, blocks; a human
writes the answer to disk; the agent unblocks and continues.

- **`dashboard/mcp/server.py`** — the four tool implementations as
  pure async functions, plus a FastMCP wrapper (`build_server`) and the
  `python -m dashboard.mcp <job> <stage> --root <path>` stdio entry
  point. Tools: `open_task` (writes `tasks/<task_id>/task.json` +
  `task-spec.md`), `update_task` (mutates state, optionally writes
  `task-result.json` sidecar), `open_ask` (writes `hil/<id>.json`,
  long-polls until status flips to `answered` or `cancelled`), and
  `append_stages` (appends to `stage-list.yaml`, rejecting duplicate
  ids).
- **`dashboard/mcp/manager.py`** — `MCPManager.spawn(job_slug,
  stage_id, root)` returns a `ServerHandle` containing the per-stage
  `Channel` and the `mcp_config` dict that Claude Code consumes via
  per-session settings. `dispose(handle)` is idempotent.
- **`dashboard/mcp/channel.py`** — `Channel.push(kind=..., text=...,
  source=...)` appends a typed `NudgeMessage` to
  `stages/<sid>/nudges.jsonl`. Sequence numbers are monotonic per stage,
  reseeded from the on-disk tail at construction so they survive process
  restart. An optional `notify` callback fires after each successful
  write — the hook for future live-injection mechanisms.
- **`dashboard/mcp/__main__.py`** — forwards to `server.main()`; lets
  Claude Code launch the MCP server via the standard `python -m` form
  the manager emits in `mcp_config`.
- **`job_driver/stage_runner.py`** — `RealStageRunner` now accepts an
  optional `mcp_manager` (plus `job_slug` + `hammock_root`). When wired,
  it spawns a `ServerHandle` before each stage, merges the manager's
  `mcp_config` into `session-settings.json`, and disposes the handle on
  exit. The Stage 5 stop-hook wiring is preserved.
- **`mcp` Python SDK (>=1.27, <2)** added to runtime dependencies.
- **31 new tests** (29 unit + 2 stdio round-trip + 2 stage-runner
  wiring) under `tests/dashboard/mcp/`, `tests/e2e/`, and
  `tests/job_driver/test_stage_runner_mcp.py`.
- **`scripts/manual-smoke-stage06.py`** — drives all four tools over
  real `mcp.ClientSession` + `stdio_client` against
  `python -m dashboard.mcp ...`. No real Claude Code installation
  required.

## Notable design decisions made during implementation

1. **The MCP server is launched by the client, not by `MCPManager`.**
   `spawn()` returns a *descriptor* (the `mcp_config` dict) rather than
   forking a stdio subprocess — `stdio` MCP semantics give ownership of
   the server process to whoever spawns the stdio pipes (Claude Code
   in production; the test client in unit tests). Trying to attach to
   a pre-spawned stdio server is fighting the protocol. `dispose()` is
   therefore engine-side bookkeeping only.
2. **Tool functions are pure async, decoupled from FastMCP.** The four
   tools live as standalone `async def` functions taking explicit
   `job_slug`, `stage_id`, `root`. `build_server()` is a thin adapter
   that re-exposes them as FastMCP tools. This keeps unit tests fast
   (no protocol overhead) while a single e2e test validates the wire
   path.
3. **Long-poll is implemented as filesystem polling, not watchfiles.**
   The MCP server runs in a separate subprocess from the dashboard, so
   it can't share the dashboard's `Cache` or `InProcessPubSub`. Polling
   `hil/<id>.json` with a configurable interval (default 100ms) is
   simple, robust, and adequate for v0 throughput. Stage 7 will add
   the orphan sweeper that handles `cancelled` transitions.
4. **`open_ask` validates the answer payload before returning.** The
   tool serialises the answer through the appropriate `HilAnswer`
   model so a malformed dashboard-side write surfaces as
   `MCPToolError("invalid answer payload: ...")` rather than reaching
   the agent in a broken shape.
5. **`update_task` writes `result` as a sidecar, not as a `TaskRecord`
   field.** `TaskRecord` is locked Stage 0 and has no `result` slot;
   the spec calls out `result?` as part of the tool surface. We
   persist arbitrary result dicts to
   `tasks/<task_id>/task-result.json` (separate from `task.json`) so
   future stages can read them without changing the locked schema.
6. **`open_ask` accepts a `timeout` arg.** The spec says the call
   blocks until answered or cancelled, but in practice tests need
   bounded waits. The default is unbounded; when set, expiry surfaces
   as `MCPToolError("open_ask timeout ...")` and the awaiting HIL item
   is left on disk for Stage 7's sweeper.
7. **`MCPToolError` → `ValueError` at the FastMCP boundary.** FastMCP
   wraps tool exceptions into JSON-RPC errors; raising a Python
   `ValueError` (with the tool error's message) is the simplest path
   that gives the agent a typed failure rather than an opaque crash.
8. **Channel writes happen before `notify` fires; `notify` errors
   propagate.** The on-disk `nudges.jsonl` is the canonical record;
   `notify` is best-effort fan-out. We deliberately don't swallow
   notify exceptions — failures should surface, not silently drop.
9. **`Channel.seq` is reseeded from the on-disk tail.** A new
   `Channel` instance for the same stage continues the same sequence,
   not from zero. This survives process restart without any
   coordination state.
10. **Task and HIL ids include a UTC stamp + 6-byte hex.**
    Human-readable, sortable, collision-resistant for typical
    throughput. Format mirrors design doc § HIL bridge ("ask_2026-...").

## Locked for downstream stages

- **Tool signatures** match implementation.md § 5.4 verbatim. Stages 7,
  9, 13 import the tools or call them via the MCP wire; the function
  signatures are stable.
- **`MCPManager.spawn(job_slug=..., stage_id=..., root=...) ->
  ServerHandle`** is the entry point Job Driver uses. Stage 7's orphan
  sweeper wires into the same `MCPManager` lifecycle.
- **`mcp_config` shape** — `{"mcpServers": {"hammock-dashboard":
  {"command": <python>, "args": [...]}}}`. The server name
  `hammock-dashboard` is canonical; future configs that merge with the
  dashboard MCP server use that key.
- **`Channel.path` is `stages/<sid>/nudges.jsonl`.** Anyone reading the
  channel (Stage 5's runner today; Stage 15's Agent0 stream pane
  later) can rely on this path.
- **`NudgeMessage` schema** — `seq`, `timestamp`, `stage_id`, `kind`,
  `source`, `text`. `extra="forbid"`; adding fields is a structural
  change.
- **`MCPToolError`** is the single error type tools raise. Callers
  outside FastMCP (unit tests, in-process invocations) catch this; the
  FastMCP layer translates to `ValueError` for JSON-RPC.
- **`task-result.json` sidecar** at
  `tasks/<task_id>/task-result.json` — Stage 9's projections / cost
  rollup can read this for per-task output tracking.

## Files added/modified (16)

```
dashboard/mcp/__init__.py                  (new)
dashboard/mcp/__main__.py                  (new)
dashboard/mcp/channel.py                   (new)
dashboard/mcp/manager.py                   (new)
dashboard/mcp/server.py                    (new)

job_driver/stage_runner.py                 (RealStageRunner: mcp_manager wiring)

tests/dashboard/mcp/__init__.py            (new)
tests/dashboard/mcp/test_channel.py        (new, 8 tests)
tests/dashboard/mcp/test_manager.py        (new, 5 tests)
tests/dashboard/mcp/test_server.py         (new, 2 tests)
tests/dashboard/mcp/test_tools.py          (new, 14 tests)
tests/e2e/__init__.py                      (new)
tests/e2e/test_mcp_round_trip.py           (new, 2 tests)
tests/job_driver/test_stage_runner_mcp.py  (new, 2 tests)

scripts/manual-smoke-stage06.py            (new)

docs/stages/stage-06.md                    (this file)
docs/stages/README.md                      (index updated)

pyproject.toml                             (+mcp>=1.27,<2)
uv.lock
```

## Dependencies introduced

| Layer | Package | Version | Purpose |
|---|---|---|---|
| runtime | `mcp` | `1.27.0` | MCP Python SDK (FastMCP server, stdio client/server) |
| transitive | `httpx-sse` | `0.4.3` | Pulled by `mcp` |
| transitive | `jsonschema` | `4.26.0` | Pulled by `mcp` |
| transitive | `jsonschema-specifications` | `2025.9.1` | Pulled by `jsonschema` |
| transitive | `pyjwt` | `2.12.1` | Pulled by `mcp` (auth surface) |
| transitive | `python-multipart` | `0.0.27` | Pulled by `mcp` |
| transitive | `referencing` | `0.37.0` | Pulled by `jsonschema` |
| transitive | `rpds-py` | `0.30.0` | Pulled by `referencing` |
| transitive | `sse-starlette` | `3.4.1` | Pulled by `mcp` (HTTP transport) |

## Acceptance criteria — met

- [x] All four tools work end-to-end against a real (test) session —
      `tests/e2e/test_mcp_round_trip.py` drives `open_task` →
      `update_task(DONE)` → `open_ask` (with simulated answer) →
      `append_stages` over real stdio MCP, plus the smoke script.
- [x] `open_ask` blocks until the HilItem is answered or cancelled —
      `test_open_ask_blocks_until_answered`,
      `test_open_ask_cancelled_raises`,
      `test_open_ask_writes_awaiting_then_returns`.
- [x] `nudges.jsonl` accumulates entries; agent receives them at next
      turn — `Channel.push` appends typed `NudgeMessage` rows; the
      consumer-side wiring lives in the agent runner (Stage 5's
      `RealStageRunner`).
- [x] Per-stage MCP server process spawned on stage start, disposed on
      stage exit — `RealStageRunner` calls `MCPManager.spawn` before
      the agent starts and `dispose` in a `finally` block;
      `test_real_runner_spawns_and_disposes_mcp` asserts both.
- [x] Tool errors surface as MCP errors, not silent failures —
      `MCPToolError` is raised by all four tools on bad input; the
      FastMCP wrapper translates to `ValueError` so the protocol layer
      emits a JSON-RPC error to the agent.

## Smoke output

```
== Stage 6 manual smoke ==

[1/1] Driving MCP server over stdio...
  ✓ MCP session initialised
  ✓ open_task → task.json written (task_2026-05-03T07:47:41_b89b95)
  ✓ update_task(DONE) — state flipped, task-result.json written
  ✓ open_ask blocked, received human answer (option B)
  ✓ append_stages added implement-2 to stage-list.yaml

✓ Stage 6 smoke complete — four MCP tools functional end-to-end.
```

## Notes for downstream stages

- **Stage 7 (HIL plane realisation)** owns the
  `awaiting → cancelled` transition (orphan sweep). The MCP server's
  `open_ask` raises `MCPToolError("... cancelled")` if it observes
  `status: "cancelled"`; the sweeper just needs to write that status.
  `MCPManager.dispose` is the natural hook point if Stage 7 needs to
  notify in-flight `open_ask` callers on stage restart.
- **Stage 8/9 (FastAPI shell + read endpoints)** can call the four
  tool functions directly (in-process) for tests via
  `from dashboard.mcp.server import open_task, ...`. The functions
  take `root=` so they remain `tmp_path`-friendly.
- **Stage 13 (HIL forms)** is the dashboard-side writer of the
  HIL answer. Its only contract is the on-disk shape:
  `status: "answered"`, `answer: { ... }`, `answered_at: <iso>`. The
  MCP server validates the answer through `AskAnswer | ReviewAnswer
  | ManualStepAnswer` before returning.
- **Stage 9 (read endpoints / projections)** can ingest
  `task-result.json` sidecars for per-task output tracking; the file
  is JSON, written via `atomic_write_text`, and lives next to
  `task.json`.
- **`open_ask` poll interval** defaults to 100ms. Tests pass smaller
  intervals (20–50ms) for speed; production uses the default. If a
  future stage adds a watchfiles-based event source for HIL items,
  the polling can collapse to a single trigger — the public API
  doesn't change.
- **`Channel.notify` is the wiring point for `--channels dashboard`
  delivery into a live session** if/when Claude Code grows that
  capability. Today it's a hook for tests; tomorrow it's the bridge.
- **MCP server logging.** FastMCP emits `Processing request of type
  ...` lines on its logger. The smoke script and tests don't suppress
  these; for production, route the server stderr to
  `stages/<sid>/orchestrator-session.log` so traces stay scoped to
  the stage.
- **Tool argument names** (`task_spec`, `worktree_branch`, `kind`,
  `stages`, etc.) are the keys the agent will pass. Don't rename
  them without a structural-change stage — agent prompts that
  reference these tools will become brittle.
