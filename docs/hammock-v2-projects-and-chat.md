# Hammock v2 — projects + visual workflow editor + 2-way orchestrator chat

Status: design — implementation in flight. Extends `hammock-v2-extras.md`.

## Why this exists

Teams don't want a single-project tool. They want:

1. **Multiple projects** managed in one Hammock instance.
2. **Workflows scoped to a project** — different projects, different workflows.
3. **Editable workflows visually** — drag-drop nodes, edit prompts inline, see the DAG render live.
4. **Live transparency** — every agent's chat (including its tool calls / "thoughts") streamed real-time.
5. **The orchestrator as a first-class entity** — its events visible alongside the per-node chat tails.
6. **2-way conversation with the orchestrator** — operator can intervene mid-run without restarting.

## 1. Project management

Mirrors v1's `/api/projects` surface:

```
POST   /api/projects                  → register (body: {slug, repo_path, name?})
GET    /api/projects                  → list with health check (path exists, is git repo)
GET    /api/projects/:slug            → detail
DELETE /api/projects/:slug            → unregister (does NOT delete the repo)
POST   /api/projects/:slug/verify     → re-run health check
```

Storage: `~/.hammock-v2/projects/<slug>.json`:

```json
{
  "slug": "highlighter-extension",
  "name": "Highlighter Chrome Extension",
  "repo_path": "/home/nitin/workspace/highlighter-extension",
  "registered_at": "2026-05-09T...",
  "default_branch": "master"
}
```

Health check: directory exists + is a git repo (`<repo_path>/.git` exists).

**Frontend**:
- New nav entry: **Projects**
- `/projects` — card list with health pip
- `/projects/:slug` — detail: repo path, default branch, list of workflows scoped here, "Submit job" CTA, delete affordance with confirmation
- `/projects/new` — register form: repo_path text input + slug auto-derived from path

## 2. Workflow + project binding

Workflows can be **bundled** (read-only, shipped with hammock) or **per-project** (under a project's repo at `<repo>/.hammock-v2/workflows/<name>.yaml`).

When submitting a job:
- Project dropdown — required
- Workflow dropdown — populated from {bundled} ∪ {project's local workflows}; per-project workflows shadow bundled with the same name
- Request textarea + artifacts (already in place)

Backend resolution at submit time:
1. Look up project by slug → get `repo_path`
2. Resolve workflow: prefer `<repo_path>/.hammock-v2/workflows/<name>.yaml` over bundled
3. Pass resolved workflow path to the runner; clone the project repo into `<job_dir>/repo` so the agents have it

API surface:
```
GET /api/projects/:slug/workflows     → list bundled + project-local for this project
POST /api/projects/:slug/workflows    → save a workflow under this project (body: {name, yaml})
PUT /api/projects/:slug/workflows/:name → update
DELETE /api/projects/:slug/workflows/:name → delete (only project-local)
```

Per-project workflow storage path: `<repo_path>/.hammock-v2/workflows/<name>.yaml`. Operator owns this dir; hammock just reads/writes.

## 3. Visual workflow editor (overhaul)

The current YAML+textarea editor is too thin. Rebuild as a **node graph editor**:

```
┌─────────────────────────────────────────────────────────────────┐
│ [+ New node]   [Save]   [Toggle YAML view]                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌──────────┐                                                  │
│   │write-bug-│                                                  │
│   │ report   │                                                  │
│   └────┬─────┘                                                  │
│        ▼                                                        │
│   ┌──────────┐         ┌──────────┐                             │
│   │write-    │────────▶│ review-  │ (human review)              │
│   │design-spec│        │design-spec│                            │
│   └──────────┘         └────┬─────┘                             │
│                             ▼                                   │
│                        ┌──────────┐                             │
│                        │implement │                             │
│                        └──────────┘                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

Features:
- **Drag-drop**: drag nodes around the canvas; topology auto-recomputes from the `after:` edges
- **Add node**: `+` button → modal with id input, prompt selector (dropdown of available prompt files + "Create new prompt..."), human-review checkbox, requires-list builder
- **Edit node**: click a node → side panel with: id, prompt picker, human_review, requires (chip list), description. Prompt content is editable INLINE in the side panel (textarea, syntax-highlighted markdown ideally but plain textarea acceptable)
- **Connect nodes**: drag from a node's output handle to another node's input handle to create an `after:` edge. Or click "Add after dependency" in the side panel.
- **Delete**: right-click or hover → trash icon
- **YAML view**: toggle in the toolbar → shows the equivalent yaml; read-only in graph mode, editable in YAML mode (edits propagate back to graph)
- **Reusable prompts**: prompts saved per-project at `<repo>/.hammock-v2/prompts/<name>.md`. Editor lists bundled ∪ project prompts. Adding a new prompt saves to project-prompts.

Implementation: use **Vue Flow** library (most popular Vue 3 graph editor). It handles drag-drop, edges, viewport, autolayout.

Backend:
- `GET /api/projects/:slug/prompts` — list bundled + project prompts
- `GET /api/projects/:slug/prompts/:name` — content
- `POST /api/projects/:slug/prompts` — save (`{name, content}`)
- `DELETE /api/projects/:slug/prompts/:name`

## 4. Real-time live chat per node (with agent's thoughts)

Today's chat tail shows only **completed turns**. Agents emit assistant messages with text + tool calls; we render those. But the user wants **agent thoughts** — the in-turn reasoning.

claude `--include-partial-messages` flag emits partial assistant blocks as the model streams. Combined with stream-json, every text chunk + tool call is visible in real time.

**Backend**:
- `runner/run_job.py` adds `--include-partial-messages` to the claude invocation for both orchestrator AND each subagent (the subagent claude calls live INSIDE the orchestrator's Task calls, so this propagates through Task)
  - Actually: the orchestrator spawns subagents via Task tool; we don't control claude flags for those.
  - **Alternative**: instead of Task, the orchestrator could invoke `claude -p` subprocesses directly via Bash, with explicit `--include-partial-messages`. Each subagent's chat.jsonl gets full streaming.
  - Adopt this. The orchestrator prompt instructs: "to spawn a subagent, run `claude -p <prompt> --output-format stream-json --verbose --include-partial-messages --permission-mode bypassPermissions` redirecting stdout to `nodes/<id>/chat.jsonl`".
  - This way the chat.jsonl streams WHILE the subagent runs, and our SSE pipeline picks up file appends every 500ms.

**Frontend**:
- AgentChatTail already SSE-subscribes to `chat_appended`. Just keep it as-is; partial messages appear as new turn entries.
- Render partial assistant text turns as "still typing" with a subtle pulse indicator.

## 5. Orchestrator pseudo-node

The orchestrator itself runs as the master claude process. Its chat is at `<job_dir>/orchestrator.jsonl`. Today it's a separate "Orchestrator" view route.

**Change**: surface the orchestrator AS a node in the left timeline. First entry. Always present. Selecting it shows:
- Tabs: Output (the orchestrator.log markdown summary) / Chat (parsed orchestrator.jsonl) / Events (job state changes)
- Live-updated via the same SSE channel

The orchestrator's "events" tab summarizes: subagent started/completed, validation result, HIL waiting, decisions received, errors. Computed from existing per-node state files + chat events.

## 6. 2-way HIL chat with the orchestrator

The biggest change. Operator wants to:
- "Skip this node" mid-run
- "Re-run X with these additional instructions"
- "Abort the job"
- "Let me see what you're doing for node Y" → the orchestrator pauses, summarizes its plan
- General mid-flight steering

**Mechanism**: a message queue file at `<job_dir>/orchestrator_messages.jsonl`. Each line is a JSON message:

```json
{"id": "msg-1", "from": "operator", "timestamp": "...", "text": "Please skip implement and write a summary explaining why."}
```

The orchestrator's prompt is updated to:
- Between every Task subagent dispatch, check `orchestrator_messages.jsonl` for new messages (track the last-processed line index in `<job_dir>/orchestrator_state.json`)
- For each new operator message: respond. The orchestrator can: continue, change course, ask back, or abort. Its responses go into the same file with `"from": "orchestrator"`.
- The orchestrator's message-checking loop runs at every state transition (between nodes, after validation, etc.) — so latency is "next checkpoint" not real-time. KISS.

**Frontend**: orchestrator pseudo-node gets a **Chat** tab that's a 2-way conversation. Renders messages from both sides. Below: input textarea + send button. Send POSTs to `/api/jobs/:slug/orchestrator/messages` which appends to the file.

API:
```
GET  /api/jobs/:slug/orchestrator/messages → all messages
POST /api/jobs/:slug/orchestrator/messages → {text} → appends operator message
```

SSE: `orchestrator_message_appended` event.

The orchestrator's response shows up as a new message line. Frontend renders chat-style.

## 7. Implementation order

Big delivery. Sub-stages, in order:

1. **Projects API + storage** (CRUD endpoints, `~/.hammock-v2/projects/`)
2. **Project picker + workflow resolution per project at submit time**
3. **Frontend: Projects list + detail + new + delete**
4. **Project-local workflows + prompts API**
5. **Visual workflow editor with vue-flow** (graph + drag-drop + side panel for node editing + prompt inline edit + yaml toggle)
6. **Orchestrator: invoke subagents via Bash claude -p** (instead of Task) for live streaming
7. **Orchestrator pseudo-node in left timeline**
8. **2-way orchestrator chat: messages file + API + frontend chat panel + orchestrator prompt updates**

Each stage commits. Push at end.

## 8. What stays compatible

- Existing v1 routes, v1 dashboard, v1 tests untouched.
- Existing v2 routes used by stage 1+2 (jobs, nodes, workflows endpoints) extended, not broken.
- The fix-bug workflow keeps working.

## 9. End-to-end target

Monday morning a developer:
1. Lands on dashboard. Sees Projects + Jobs + Workflows nav.
2. Goes to Projects, clicks New, registers `highlighter-extension`.
3. Goes to Workflows, opens fix-bug, clicks "Save as new in highlighter-extension," edits a prompt inline, saves.
4. Submits a job: picks project + their custom workflow + types request + drops 2 attachments.
5. Lands on job detail. Orchestrator pseudo-node first in timeline; clicks it, sees orchestrator's plan in chat.
6. Watches each subagent stream its thoughts as it works.
7. At human-review gate, types "approve, but mention the COLORS array constraint" in the orchestrator chat. Orchestrator picks up, re-spawns the implementer with that note.
8. Implementer creates branch + commits + opens PR.
9. Job completes. Summary in the last node's output panel.

That's the v2 vision delivered.
