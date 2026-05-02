# HIL bridge + MCP tool surface — section to integrate

> **Where this goes:** insert between the `## Observability and Meditation` section
> and the `## Boundary contracts (high-level)` section. The iteration log entry at
> the bottom of this file appends to the table at the end of the document.

---

## HIL bridge and MCP tool surface

This section pins down the agent ↔ dashboard interface: the MCP tools the dashboard exposes, the typed shapes that flow through them, and the lifecycle of HIL items. It builds on the HIL plane abstraction (Object-level planes), the two-mechanism HIL split (Stage as universal primitive § *HIL is two distinct mechanisms, not one*), and the communication channels in Execution architecture.

### MCP tool surface

The dashboard MCP server exposes four tools to running CLI sessions. Three are non-blocking; `open_ask` is a long-polling tool that does not return until the human answers (or the item is cancelled).

| Tool | Args | Returns | Blocks? |
|---|---|---|---|
| `open_task` | `stage_id`, `task_spec`, `worktree_branch` | `{task_id}` | No |
| `update_task` | `task_id`, `status: RUNNING\|DONE\|FAILED`, `result?` | `{ok}` | No |
| `open_ask` | `kind`, `stage_id`, `task_id?`, kind-specific question fields | The matching `HilAnswer` | **Yes (long-poll)** |
| `append_stages` | `stages: list[StageDefinition]` | `{ok, count}` | No |

**Notes.**

- `open_ask` is unified across all three HIL kinds (`ask`, `review`, `manual-step`) via a discriminated union on `kind`. Same lifecycle, same storage, same blocking semantics — only the question and answer schemas differ.
- `update_task` is needed because the engine cannot reliably derive task completion from observed Task tool results alone. The agent decides when a task is "done" — it may need to verify outputs, re-run validators, or interpret partial results before declaring success. The agent's explicit signal is the source of truth.
- `append_stages` is what expander stages (e.g., `impl-spec-author`) use to extend `stage-list.yaml` at runtime.
- **Engine nudges and free-form human chat are not tools.** They flow into the session via `--channels dashboard` push (writer: dashboard MCP server; storage: `nudges.jsonl`). The agent receives them at the next turn boundary; whether to reply is the agent's choice.
- Reading stage inputs is not a tool. Input paths are inlined into the agent's prompt at session-spawn time by the Job Driver, so the agent already has them.

### HIL typed shapes

One envelope, three kinds, discriminated union by `kind`. One `HilItem` per file at `jobs/<id>/hil/<item_id>.json`.

```python
class HilItem(BaseModel):
    id: str                          # e.g. "ask_2026-04-30T14:32_a3f9"
    kind: Literal["ask", "review", "manual-step"]
    stage_id: str
    task_id: Optional[str]           # None for inter-stage HIL
    created_at: datetime
    status: Literal["awaiting", "answered", "cancelled"]
    question: HilQuestion            # discriminated union by kind
    answer: Optional[HilAnswer]      # populated when status == "answered"
    answered_at: Optional[datetime]

# --- kind: "ask" ---
# Used during design or implementation when the agent needs human input.
class AskQuestion(BaseModel):
    kind: Literal["ask"] = "ask"
    text: str
    options: Optional[list[str]]     # if present, UI surfaces as choices

class AskAnswer(BaseModel):
    kind: Literal["ask"] = "ask"
    choice: Optional[str]            # selected option (None if no options)
    text: str                        # always present, free-form

# --- kind: "review" ---
# Used at workflow gates: human approves or rejects an artifact.
class ReviewQuestion(BaseModel):
    kind: Literal["review"] = "review"
    target: str                      # path / PR URL / artifact reference
    prompt: str                      # framing question

class ReviewAnswer(BaseModel):
    kind: Literal["review"] = "review"
    decision: Literal["approve", "reject"]
    comments: str                    # always present

# --- kind: "manual-step" ---
# Used when the human's job is to do something out-of-band and report back.
class ManualStepQuestion(BaseModel):
    kind: Literal["manual-step"] = "manual-step"
    instructions: str
    extra_fields: Optional[dict]     # stage-configurable extra schema

class ManualStepAnswer(BaseModel):
    kind: Literal["manual-step"] = "manual-step"
    output: str                      # always present, free-form
    extras: Optional[dict]           # populated if extra_fields was set
```

The pattern across kinds: every answer carries at least one always-present free-form text field, even when a structured component is also present. Choice without context is rarely enough; structured-only review forfeits the human's ability to communicate nuance.

### HIL lifecycle

Three states. No expiry. `cancelled` is reserved for crash-orphan sweep, not a normal flow.

```
            ┌──────────┐
   create   │ awaiting │
   ────────▶│          │
            └────┬─────┘
                 │
        ┌────────┴────────┐
        │                 │
        ▼                 ▼
   ┌──────────┐    ┌───────────┐
   │ answered │    │ cancelled │
   └──────────┘    └───────────┘
   (terminal)      (terminal)
```

| Transition | Trigger | Writer |
|---|---|---|
| (none) → `awaiting` | Agent calls `open_ask` | Dashboard MCP server creates `hil/<id>.json` |
| `awaiting` → `answered` | Human submits answer via dashboard UI | Dashboard appends `answer` field, sets `status` |
| `awaiting` → `cancelled` | Stage restart after Agent0 crash | Hammock runner sweeps all `awaiting` items belonging to that stage |

`answered` and `cancelled` are terminal. There is no path from `cancelled` back to `awaiting`. A restarted stage's new Agent0 creates fresh HIL items if it needs to ask again.

### The blocking model — why `open_ask` blocks

`open_ask` is the only MCP tool in hammock that does not return immediately. It long-polls until the human answers (or the item is cancelled), then returns the `HilAnswer` directly as the tool's return value. The agent does not have to handle wire-level mechanics — the MCP tool blocks; the agent's turn is paused; the next turn resumes with the answer in hand.

This matters because of subagents. A subagent dispatched via the Task tool runs as a single Task invocation inside Agent0. It has no natural pause-and-resume primitive: when its work completes, the Task tool returns and the subagent is gone. If `open_ask` returned immediately and the answer were delivered later via `--channels`, only Agent0 could use it; subagents could not. Making `open_ask` blocking gives both Agent0 and subagents a single uniform way to ask for human input.

The corollary: **`--channels` is reserved for traffic that does not correspond to a structured ask.** Engine nudges (task failure, budget warning), free-form human chat, and any other "interrupt the running session with a message" use case all flow through the channel. Structured asks flow through `open_ask`. The two never overlap.

### Inter-stage HIL realisation

A workflow gate (spec review, PR merge, plan kickoff) is a stage with `agent_config.role: human-gatekeeper`. Mechanically:

```yaml
# Stage definition snippet
id: spec-review-human
agent_config:
  role: human-gatekeeper
  prompt: |
    Your job is to obtain a human review decision for {target}.
    Call open_ask with kind="review", target="{target}",
    prompt="{prompt}". When the answer comes back, write it
    verbatim to {output_path}, then exit.
inputs: [spec.md]
outputs: [spec-reviews/human/decision.json]
```

Agent0 spawns, calls `open_ask(kind="review", target="spec.md", prompt="Approve this spec?")`, blocks on the long-poll. The dashboard surfaces the review UI. The human approves or rejects with comments. The tool returns. Agent0 writes `decision.json` and exits cleanly. Stage = `SUCCEEDED`.

This costs a few thousand tokens per gate (Agent0's startup overhead for one tool call), which for a typical e2e-feature job's ~5 gates is negligible. The benefit is architectural: every stage has the same shape, runs the same machinery, and uses the same observability. There is no separate "human worker" subprocess, no special-cased Job Driver code path for human stages.

### Crash semantics

| Failure | Response | Rationale |
|---|---|---|
| **SubAgent crashes** (Task tool result is FAILED or times out) | Agent0's responsibility. Agent0 retries, modifies the task spec, escalates via `open_ask`, or marks the task FAILED via `update_task`. Engine does not intervene. | Subagents are Agent0's leverage. Agent0 chose to dispatch them, so Agent0 owns the failure handling. |
| **Agent0 crashes** (CLI session exits unexpectedly) | Job Driver detects via process exit. Hammock runner restarts the stage from scratch. v0 does not attempt to resume from saved Agent0 state. | Restart-from-scratch is simple, correct, and bounded. Resume-from-state is a v1+ feature gated on agent-side checkpointing primitives. |
| **HIL items orphaned by Agent0 crash** | When the stage restarts, the runner sweeps all `awaiting` HIL items belonging to that stage to `cancelled`. The new Agent0 creates fresh HIL items as needed. | Cancellation retains the record for debugging without confusing the dashboard's open-items view. |

### Notification routing in v0

v0 surfaces all HIL items through the **dashboard UI only**. Telegram (and any other out-of-band notification carrier) is deferred to **v1 backlog**. The HIL plane's typed shapes and lifecycle are independent of carrier; adding Telegram in v1 is a new write-out path on the dashboard side, not a change to the HIL contract.

### Decisions captured here

- **MCP tool surface — four tools.** `open_task`, `update_task`, `open_ask`, `append_stages`. Engine nudges and chat are not tools; they're channel pushes.
- **`open_ask` is the only blocking tool.** Long-poll inside the MCP layer. The agent's turn is paused; the answer is the tool's return value.
- **One unified `open_ask` for all HIL kinds**, discriminated union by `kind`. Same lifecycle, same storage.
- **HIL shapes — three kinds, every answer has an always-present free-form text field.** `ask` (text + optional choice), `review` (approve/reject + comments), `manual-step` (output + optional stage-defined `extras`).
- **HIL lifecycle — three states.** `awaiting`, `answered`, `cancelled`. No expiry. `cancelled` reserved for crash-orphan sweep.
- **Pattern A — blocking long-poll, not channel-delivered answers.** Required so subagents can use HIL the same way Agent0 does.
- **Inter-stage HIL = Agent0 calling `open_ask`.** Architectural consistency over the few thousand tokens a special-cased path would save.
- **Crash semantics — Agent0 owns subagent failures; runner restarts on Agent0 crash; orphaned HIL items go to `cancelled`.**
- **Notification routing v0 — UI only.** Telegram in v1 backlog; HIL contract carrier-agnostic.

---

## Iteration log entry — append this row

```
| 2026-04-30 | Added **HIL bridge and MCP tool surface** section. (1) **MCP tool surface** locked at four tools — `open_task`, `update_task`, `open_ask`, `append_stages`; `open_ask` is the only blocking one; engine nudges and chat are channel pushes via `--channels`, not agent-callable tools; reading stage inputs is not a tool because the Job Driver inlines input paths into the agent prompt at spawn. (2) **HIL typed shapes** — one `HilItem` envelope, three kinds (`ask`, `review`, `manual-step`) via discriminated union on `kind`. Every answer carries at least one always-present free-form text field. `ask` = text + optional choice from `options`; `review` = approve/reject + comments; `manual-step` = output + stage-configurable `extras`. (3) **Lifecycle** — three states (`awaiting`, `answered`, `cancelled`), no expiry; `cancelled` is reserved for stage-restart orphan sweep, not a normal flow. (4) **Pattern A — blocking long-poll** committed: `open_ask` does not return until the human answers; the answer is the tool's return value. Required so subagents (which have no pause-and-resume primitive within a single Task invocation) can use HIL the same way Agent0 does. The `--channels` push mechanism is reserved for engine nudges and free-form chat — never for HIL answers. (5) **Inter-stage HIL = Agent0 with `agent_config.role: human-gatekeeper`** that spawns, calls `open_ask`, writes the answer, and exits. Same machinery as agent stages; architectural consistency over the few thousand tokens saved by a special-cased path. (6) **Crash semantics** — SubAgent failures are Agent0's responsibility; Agent0 crashes trigger stage restart from scratch in v0 (resume-from-state is v1+); orphaned `awaiting` HIL items in restarted stages are swept to `cancelled` for debuggability. (7) **Notification routing v0 — dashboard UI only.** Telegram and other out-of-band carriers added to v1 backlog. HIL contract is carrier-agnostic; v1 adds a write-out path on the dashboard, not a change to the HIL contract. |
```
