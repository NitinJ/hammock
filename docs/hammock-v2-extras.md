# Hammock v2 — extras (reliability + UX)

Status: design — implementation in flight. Extends `docs/hammock-v2-design.md` with requirements gathered after the first e2e validation.

## Why this exists

The first v2 e2e run validated the orchestrator-as-agent pattern. This pass adds the things teams need before they'd actually use Hammock:

1. **Strict output validation** — the orchestrator can't just trust the agent's word. It must verify on disk.
2. **Multi-artifact submission** — real bug-fix tasks come with screenshots, error logs, design docs attached. The first node must receive them.
3. **Workflows as a first-class dashboard surface** — operators can see, visualize, edit, and manage them.
4. **Real-time observability** — orchestrator + agent chat streams should update live.
5. **Proper HIL integration** — the textarea-via-API approach is too thin. Operators want a form with the agent's review on one side, an approve/revise control on the other.

## 1. Strict output validation

### Schema change (workflow.yaml)

Each node may declare `requires:` — a list of files (relative to the node's folder) that must exist + be non-empty before the node is marked succeeded.

```yaml
nodes:
  - id: write-bug-report
    prompt: write-bug-report
    requires:
      - output.md          # the agent's narrative
      - bug-report.json    # structured fields the next node consumes (optional)

  - id: implement
    prompt: implement
    requires:
      - output.md
      - branch.txt         # the branch name the implementer created
```

Default `requires:` is `["output.md"]`. Workflow author overrides for richer contracts.

### Orchestrator behavior

After each Task subagent returns, the orchestrator runs a strict check:

1. For every path in `requires:`, verify file exists at `nodes/<id>/<path>` AND `len(read_bytes()) > 0`.
2. If any fail: collect the missing/empty list, write `state: failed` with `error: "missing required outputs: [...]"`, log to `validation.md` under the node folder.
3. **No semantic check.** The orchestrator does NOT read content to "verify quality" — only existence + non-empty. Agents are responsible for content.

### Retry policy

If validation fails on first attempt:
1. Re-spawn the Task with the missing-output list as additional context: "Your previous attempt did not produce the required files: <list>. Please write all required outputs."
2. Re-validate.
3. If second attempt also fails: hard-fail the node, write `state: failed`, mark job as `failed`. No third attempt — surface the failure to the operator.

## 2. Multi-artifact submission

### Job submit API

```
POST /api/jobs
multipart/form-data:
  workflow: "fix-bug"
  request: "Add a docstring to add_integers..."
  artifacts: [<file>, <file>, ...]    # zero or more
```

Backend: writes each uploaded file to `<job_dir>/inputs/<filename>` (sanitized).

### First-node input.md

The orchestrator builds the first node's `input.md` with two sections:

```markdown
## Request

<request text>

## Attached artifacts

- `inputs/screenshot-001.png` — image, 124KB
- `inputs/error-log.txt` — text, 4.2KB, first 40 lines below
- `inputs/design-old.md` — markdown, 3.1KB, full content below

### inputs/error-log.txt (first 40 lines)
\`\`\`
...
\`\`\`

### inputs/design-old.md (full)
<verbatim content>
```

Image/binary files are listed but not inlined; the agent can `Read` them via the path.
Text files under 2KB are inlined fully; over 2KB get the first 40 lines preview + path.

### Frontend submit form

- Workflow dropdown
- Request textarea
- Drag-drop zone for files (multiple). Show file list with size + remove button.
- Submit → POST as multipart.

## 3. Workflows section in the dashboard

New nav entry: **Workflows** (alongside Jobs).

### List view (`/workflows`)

Card grid. Each card:
- Name + description
- Node count
- Mini DAG preview (just dots + lines, no labels)
- "View" button → detail page

### Detail view (`/workflows/:name`)

Top: name, description, "Use this workflow" button (jumps to `/jobs/new` with this workflow preselected).

Center: **DAG visualizer** rendered as an SVG. Use a simple layered-layout algorithm:
- Topological levels left-to-right
- Each node a rounded rectangle with id + (human-review badge if applicable)
- Edges as smooth curves between levels
- Pan/zoom not required for v1; static fit-to-pane is enough

Below: yaml source view (read-only, syntax highlighted).

### Editor (`/workflows/:name/edit` or `/workflows/new`)

Two-pane layout:
- Left: monaco editor (or simple textarea if monaco is too heavy) with the yaml
- Right: live DAG preview (re-render on yaml changes, debounced 500ms)

Save button. Validates yaml on save:
- Schema check (Pydantic load)
- DAG check (no cycles, all `after:` refs valid)
- Prompt-existence check (every node's `prompt:` resolves to a file in `prompts/`)

Saved workflows go into `~/.hammock-v2/workflows/<name>.yaml` (the user's local). Bundled workflows in `hammock_v2/workflows/` are read-only — the editor opens them as "Save as new name".

### Backend endpoints

- `GET /api/workflows` — list (already exists)
- `GET /api/workflows/:name` — detail (yaml + parsed)
- `POST /api/workflows` — create (body: `{name, yaml}`)
- `PUT /api/workflows/:name` — update (body: `{yaml}`)
- `DELETE /api/workflows/:name` — delete (only user-defined; bundled = 405)

## 4. Real-time observability

Replace vue-query polling with SSE-driven invalidation throughout the job-detail surface.

### SSE event types

Backend emits events on file mtime changes under `<job_dir>/`:

- `node_state_changed` — `{slug, node_id, new_state}`
- `chat_appended` — `{slug, node_id}`
- `orchestrator_appended` — `{slug}`
- `awaiting_human` — `{slug, node_id}`
- `human_decision_received` — `{slug, node_id}`
- `job_state_changed` — `{slug, new_state}`

Each fires within 500ms of the file change (existing 500ms coalesce window).

### Frontend wiring

`composables/useJobStream.ts` opens an `EventSource` on `/sse/jobs/:slug`. On each event, invalidate the corresponding vue-query keys.

The chat tail component subscribes specifically to `chat_appended` events for the currently-displayed `(node_id)` and refetches.

The orchestrator chat tab subscribes to `orchestrator_appended` and refetches.

The left-pane node list re-renders on `node_state_changed`.

When a job is in `running` state, the dashboard shows a subtle live indicator (small green dot) next to the job state pill.

## 5. Proper HIL integration

When a node is `awaiting_human: true`, the right pane swaps to a structured form:

```
┌─────────────────────────────────────────────────────┐
│ Awaiting your review                                │
│                                                     │
│ Agent's review:                                     │
│ ┌─────────────────────────────────────────────────┐ │
│ │ [renders nodes/<id>/output.md as markdown]      │ │
│ │                                                 │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│ Your decision:                                      │
│ ( ) Approve                                         │
│ ( ) Needs revision                                  │
│                                                     │
│ Comment (required for needs-revision):              │
│ ┌─────────────────────────────────────────────────┐ │
│ │                                                 │ │
│ │                                                 │ │
│ └─────────────────────────────────────────────────┘ │
│                                                     │
│                              [Cancel]   [Submit]    │
└─────────────────────────────────────────────────────┘
```

API: `POST /api/jobs/:slug/nodes/:id/human_decision` with `{decision: "approved" | "needs-revision", comment: ""}`.

Backend writes `nodes/<id>/human_decision.md`:

```markdown
---
decision: needs-revision
decided_at: 2026-05-08T...
---

This is fine but please add references to the existing styles.css patterns.
```

### Orchestrator behavior on needs-revision

On `decision: needs-revision`:
1. Read the comment.
2. Re-spawn the Task subagent with: original prompt + agent's previous output + reviewer's comment + instruction to revise.
3. Subagent writes a fresh `output.md`.
4. Re-emit `awaiting_human.md` (this is iteration 2 of human review).
5. Loop until approved or max-3-revisions reached. After 3, mark node failed with `error: "human review max revisions exceeded"`.

## 6. Implementation order

This is one big delivery. Sub-stages:

1. **Schema + orchestrator validation** (most important)
2. **Multi-artifact API + first-node input.md building**
3. **Workflows section: list + detail + DAG visualizer**
4. **Workflows editor**
5. **SSE wiring across frontend**
6. **HIL form + needs-revision loop**

Each stage incrementally testable. PR pushes to `hammockv2` branch (extending PR #58).

## 7. What's deferred

- Workflow versioning (treat each save as overwriting; no history yet)
- Per-team workspaces (single-user assumption holds)
- Live workflow editor with autocomplete on node IDs (yaml textarea + live DAG is enough)
- Custom prompt files per workflow (use bundled prompts; copying for editing comes later)
- Job-level retry from a failed node (re-submit from scratch is the v2 workflow)

## 8. End-to-end target

A team operator:
1. Opens the dashboard.
2. Clicks Workflows. Sees the bundled `fix-bug` plus any they've authored.
3. Picks fix-bug, clicks "Use this workflow."
4. Drags in 3 attachments + types a request.
5. Submits. Lands on the job detail page.
6. Watches the orchestrator chat update live as it spawns subagents.
7. Each node's chat tail updates live as it runs.
8. Hits the human-review gate. Sees the agent's review rendered as markdown. Picks "Needs revision," writes a comment.
9. Watches the agent revise. Approves the second iteration.
10. Implementation runs, PR opens, summary lands. Job state goes `completed`.

That's the v2 experience.
