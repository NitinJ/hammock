# Stage 15 — Stage live view + Agent0 stream pane

**PR:** [#19](https://github.com/NitinJ/hammock/pull/19) (merged 2026-05-03)
**Branch:** `feat/stage-15-stage-live-view`
**Commit on `main`:** `bea5372`

## What was built

The Stage Live view: the most complex view in the Hammock dashboard. Operators can watch a running stage in real-time — Agent0's prose, tool calls, engine nudges, human nudges, sub-agent regions, and agent replies scroll in the centre pane as they arrive over SSE. The left pane shows the task list with live state badges. The right pane shows cost vs. budget and stage metadata. Operators can cancel the stage, restart it (up to 3 times), or send a freeform nudge via the chat input.

### Backend

- **`dashboard/api/chat.py`** (new) — `POST /api/jobs/{job_slug}/stages/{stage_id}/chat`. Accepts `ChatRequest(text: str, min_length=1)`. Writes the nudge via `Channel(job_slug, stage_id, root=root)` which appends to `nudges.jsonl`. Returns `ChatResponse(seq, timestamp, kind, text)`. Empty text rejected at the Pydantic level (422).
- **`dashboard/api/stages.py`** (extended) — two new sub-resource endpoints:
  - `POST /api/jobs/{job_slug}/stages/{stage_id}/cancel` — calls `ipc.write_cancel_command(job_slug, root=root, reason="human")` which writes `human-action.json`. Returns `CancelResponse(ok=True)`.
  - `POST /api/jobs/{job_slug}/stages/{stage_id}/restart` — checks `stage.restart_count >= MAX_STAGE_RESTARTS` (3), returns 409 if exceeded. Calls `await lifecycle.spawn_driver(job_slug, root=root)` (double-fork), returns `RestartResponse(job_driver_pid=pid)`.
- **`dashboard/api/__init__.py`** (modified) — added `chat_router`.

### Frontend composable

- **`src/composables/useAgent0Stream.ts`** — `EventSource`-backed reactive stream composable.
  - Opens `/sse/stage/{jobSlug}/{stageId}`.
  - **Deduplication:** `Set<string>` keyed on `${source}:${seq}`.
  - **Ordering:** Binary-search insert (`bisectByTimestamp`) keeps the array chronological at O(log n).
  - **Out-of-order tolerance:** Events within 500ms of previous tail are inserted at the correct chronological position; events >500ms late are appended (acceptable for v0 live tails).
  - **Auto-scroll:** `stickToBottom` ref; `newCount` increments when user has scrolled away so the UI can show a "↓ N new" indicator.
  - **Filters:** `hideToolCalls`, `hideEngineNudges`, `proseOnly` — applied via `filteredEntries` computed.

### Frontend components (11 new / 1 modified)

| Component | Description |
|---|---|
| `ProseMessage.vue` | Renders `agent0_prose` text with formatted timestamp |
| `ToolCall.vue` | Renders `tool_invoked`/`tool_result` with ▸ prefix and computed duration label |
| `EngineNudge.vue` | Renders engine nudge with ⚙ prefix |
| `HumanChat.vue` | Renders human nudge with "You" label |
| `AgentReply.vue` | Renders agent reply with "Agent" label |
| `SubAgentRegion.vue` | Collapsible region showing subagent ID, message/tool-call counts, cost, state badge; toggles on `[data-toggle]` click, shows `[data-expanded]` content |
| `ChatInput.vue` | `<form>` with `<textarea>` and submit `<button type="submit">`; emits `"send"` with trimmed text; rejects empty; clears on submit |
| `StreamFilters.vue` | Three checkboxes (`hideToolCalls`, `hideEngineNudges`, `proseOnly`); v-model with `update:modelValue` emit |
| `TasksPanel.vue` | Lists `TaskRecord[]`; shows "No tasks" when empty; uses `StateBadge` |
| `BudgetBar.vue` | `role="progressbar"`, `pct = min(100, round(costUsd/budgetUsd * 100))`; red ≥90%, yellow ≥70%, primary otherwise |
| `Agent0StreamPane.vue` | Orchestrates stream components; wires `useAgent0Stream`; auto-scrolls; calls `POST …/chat` on send |
| `StateBadge.vue` | (modified) replaced `OPEN`/`IN_PROGRESS` with `STUCK` to match backend `TaskState` |

- **`src/views/StageLive.vue`** (full implementation from stub) — three-pane layout (`data-pane="left"`, `data-pane="centre"`, `data-pane="right"`). Fetches `GET /api/jobs/{slug}/stages/{sid}` → `StageDetail` on mount. Cancel and Restart buttons call their respective POST endpoints.

### Type schema sync

- **`src/api/schema.d.ts`** — `TaskState` updated from `"OPEN" | "IN_PROGRESS" | ...` to `"RUNNING" | "BLOCKED_ON_HUMAN" | "STUCK" | ...` to match the Python model. Added `TaskRecord`, `StageRun`, `StageDetail` interfaces.

### Tests

- **16 Python backend tests** in `tests/dashboard/api/test_stage_actions.py` — `TestStageChat` (7 tests: happy path, disk write, sequential seq increment, empty text, missing field, 404 job, 404 stage), `TestStageCancel` (4 tests: 200, file write, 404 job, 404 stage), `TestStageRestart` (5 tests: 200+pid, pid file, restart count exceeded 409, 404 job, 404 stage)
- **16 frontend unit tests** in `tests/unit/composables/useAgent0Stream.spec.ts` — SSE URL, chronological insert, out-of-order within/outside 500ms, idempotency, different-source non-dedup, stickToBottom states, newCount increment, resetNewCount, 4 filter tests
- **10 frontend component spec files** (new) — ProseMessage, ToolCall, EngineNudge, HumanChat, AgentReply, SubAgentRegion, ChatInput, StreamFilters, Agent0StreamPane, StageLive
- **353 Python dashboard tests** total (all passing); **160 frontend tests** total (all passing)
- vue-tsc: 0 errors; ruff: clean; pyright (dashboard/): 0 errors

## Notable design decisions made during implementation

1. **Binary-search insert for stream ordering.** The `useAgent0Stream` composable maintains a chronologically sorted `entries` array using `bisectByTimestamp`. This keeps rendering order correct even when SSE events arrive slightly out of order, without a full sort on every event (O(log n) locate, O(n) splice — acceptable for event counts in the hundreds).

2. **Deduplication key is `${source}:${seq}`, not just `seq`.** Agent0 events and subagent events share the same SSE stream but have independent sequence counters. Keying only on `seq` would cause agent0 seq=5 to deduplicate against a subagent seq=5 from a different source. The compound key avoids this.

3. **500ms out-of-order tolerance with tail-append fallback.** True late-arriving events (>500ms behind the current tail) are appended rather than inserted in sorted position. This is the common case for replay events that are delivered fast and then live events that arrive at real time; they almost always arrive in order. Inserting truly late events would shift already-rendered rows and cause visual churn. For v0 the tail-append fallback is acceptable.

4. **`MAX_STAGE_RESTARTS = 3` enforced at the API layer, not the driver.** The driver is also responsible for tracking `restart_count`, but the API is the human-facing gate. Returning 409 at the API layer gives the operator a clear message before the driver even starts.

5. **`spawn_driver` uses double-fork to avoid zombie processes.** The intermediate process exits immediately, making the grandchild an orphan that init/systemd reaps. This lets the API return quickly without waiting for the driver to complete.

6. **`StageState | TaskState` union for `SubAgentRegion.state` prop.** Subagents can be in any stage state (RUNNING, SUCCEEDED, FAILED, etc.) but also sometimes tracked as tasks. Using a union type satisfies `StateBadge`'s type constraint without losing type safety.

7. **No virtual scroll in v0.** `vue-virtual-scroller` was not installed in the project. For the expected event count (hundreds, not millions), a plain scrollable `<div>` with auto-scroll anchor is sufficient. Virtual scroll is a future optimization.

8. **`StateBadge` task states synced to backend model.** The previous `StateBadge` STATE_CONFIG used `OPEN`/`IN_PROGRESS` which are not real `TaskState` values — they were never reachable states. Replaced with `STUCK` and aligned `TaskState` in `schema.d.ts` to match the Python enum.

9. **vue-tsc catches implicit `beforeEach` returns.** `beforeEach(() => setActivePinia(createPinia()))` returns the `Pinia` instance, which vue-tsc rejects as `not assignable to Awaitable<HookCleanupCallback>`. vitest ignores this; vue-tsc does not. All 10 new spec files use the block-body form `() => { setActivePinia(createPinia()); }`.

## Locked for downstream stages

- **`/sse/stage/{job_slug}/{stage_id}` SSE wire format is stable.** Events have `{seq, timestamp, event_type, source, job_id, stage_id, task_id, subagent_id, parent_event_seq, payload}`. `useAgent0Stream` relies on `seq` being present on replay events.
- **`POST /api/jobs/{slug}/stages/{sid}/chat` → `ChatResponse(seq, timestamp, kind, text)` is stable.** Downstream UI can expect these fields.
- **`GET /api/jobs/{slug}/stages/{sid}` → `StageDetail{job_slug, stage: StageRun, tasks: TaskRecord[]}` is stable.**
- **`MAX_STAGE_RESTARTS = 3`.** Hardcoded. To make configurable, add to `JobConfig` (shared) and thread it into `stages.py`.
- **`TaskState` values are `"RUNNING" | "BLOCKED_ON_HUMAN" | "STUCK" | "DONE" | "FAILED" | "CANCELLED"`.** Adding new states requires updating `StateBadge.vue` STATE_CONFIG and `StateBadge.spec.ts`.

## Files added/modified (32)

```
dashboard/api/chat.py                                                  (new)
dashboard/api/stages.py                                                (modified — cancel + restart endpoints)
dashboard/api/__init__.py                                              (modified — include chat_router)

dashboard/frontend/src/api/schema.d.ts                                 (modified — TaskState, TaskRecord, StageRun, StageDetail)
dashboard/frontend/src/composables/useAgent0Stream.ts                  (new)
dashboard/frontend/src/components/stage/ProseMessage.vue               (new)
dashboard/frontend/src/components/stage/ToolCall.vue                   (new)
dashboard/frontend/src/components/stage/EngineNudge.vue                (new)
dashboard/frontend/src/components/stage/HumanChat.vue                  (new)
dashboard/frontend/src/components/stage/AgentReply.vue                 (new)
dashboard/frontend/src/components/stage/SubAgentRegion.vue             (new)
dashboard/frontend/src/components/stage/ChatInput.vue                  (new)
dashboard/frontend/src/components/stage/StreamFilters.vue              (new)
dashboard/frontend/src/components/stage/TasksPanel.vue                 (new)
dashboard/frontend/src/components/stage/BudgetBar.vue                  (new)
dashboard/frontend/src/components/stage/Agent0StreamPane.vue           (new)
dashboard/frontend/src/views/StageLive.vue                             (modified — full implementation from stub)
dashboard/frontend/src/components/shared/StateBadge.vue                (modified — OPEN/IN_PROGRESS→STUCK)

tests/dashboard/api/test_stage_actions.py                              (new — 16 tests)

dashboard/frontend/tests/unit/composables/useAgent0Stream.spec.ts      (new — 16 tests)
dashboard/frontend/tests/unit/components/stage/ProseMessage.spec.ts    (new)
dashboard/frontend/tests/unit/components/stage/ToolCall.spec.ts        (new)
dashboard/frontend/tests/unit/components/stage/EngineNudge.spec.ts     (new)
dashboard/frontend/tests/unit/components/stage/HumanChat.spec.ts       (new)
dashboard/frontend/tests/unit/components/stage/AgentReply.spec.ts      (new)
dashboard/frontend/tests/unit/components/stage/SubAgentRegion.spec.ts  (new)
dashboard/frontend/tests/unit/components/stage/ChatInput.spec.ts       (new)
dashboard/frontend/tests/unit/components/stage/StreamFilters.spec.ts   (new)
dashboard/frontend/tests/unit/components/stage/Agent0StreamPane.spec.ts (new)
dashboard/frontend/tests/unit/views/StageLive.spec.ts                  (new)
dashboard/frontend/tests/unit/components/shared/StateBadge.spec.ts     (modified — updated task states)
```

## Acceptance criteria — met

- [x] `POST /api/jobs/{slug}/stages/{sid}/chat` writes nudge to `nudges.jsonl`, returns seq + timestamp
- [x] Empty chat text → 422
- [x] `POST …/cancel` writes `human-action.json` with `command=cancel`
- [x] `POST …/restart` spawns new job driver, returns pid; 409 when restart_count ≥ 3
- [x] `useAgent0Stream` opens correct SSE URL, deduplicates, inserts chronologically, filters correctly
- [x] All 11 stage components render and behave per spec (collapse/expand, emit send, filter toggles, etc.)
- [x] `StageLive.vue` renders three-pane layout, fetches `StageDetail`, cancel/restart buttons call correct endpoints
- [x] `StateBadge` state set aligned with backend `TaskState`
- [x] 353 Python dashboard tests pass; 160 frontend tests pass; vue-tsc 0 errors; ruff clean

## Notes for downstream stages

- **Auto-scroll "N new" indicator:** `useAgent0Stream.newCount` is incremented when `stickToBottom=false` and new events arrive. `Agent0StreamPane` resets it when the user scrolls back to bottom. A downstream stage could surface this as a "↓ 5 new" floating button over the stream.
- **Subagent drill-down:** `SubAgentRegion` currently shows counts and state but no nested messages. A future stage could add a `SubAgentStreamPane` inside the expanded region that opens a filtered SSE stream for `subagent_id == X`.
- **Budget bar is purely presentational:** `BudgetBar` accepts `costUsd` and `budgetUsd` as props. The `budgetUsd` value is currently sourced from `StageDetail`. If job-level vs. stage-level budget tracking is needed, the prop contract doesn't change — only the source value.
- **`restart_count` exposed in `StageRun`:** `StageLive.vue` can show "Restart 1/3" in the right pane using `stageDetail.stage.restart_count` and `MAX_STAGE_RESTARTS`. Not yet implemented; the data is available.
- **HIL integration point:** When a stage enters `BLOCKED_ON_HUMAN`, the tasks list will show a task in `BLOCKED_ON_HUMAN` state. A downstream stage could add a link from that task row to `/hil/{item_id}` using the task's `task_id` to look up the HIL item.
