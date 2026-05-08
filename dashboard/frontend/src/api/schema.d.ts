/**
 * Frontend types mirroring the v1 backend response models in
 * `dashboard/state/projections.py`. Hand-authored so the editor has
 * autocomplete; run `pnpm schema:sync` against a running dashboard to
 * regenerate from `/openapi.json`.
 */

// ── Enums ────────────────────────────────────────────────────────────────

export type JobState =
  | "submitted"
  | "running"
  | "blocked_on_human"
  | "completed"
  | "failed"
  | "cancelled";

export type NodeRunState = "pending" | "running" | "succeeded" | "failed" | "skipped";

export type NodeKind = "artifact" | "code";

export type NodeActor = "agent" | "human" | "engine";

export type HilKind = "explicit" | "implicit";

// ── Job projections ──────────────────────────────────────────────────────

export interface JobListItem {
  job_slug: string;
  workflow_name: string;
  state: JobState;
  submitted_at: string;
  updated_at: string;
  repo_slug: string | null;
}

/** Top-level rows have iter=[], loop_path=[], parent_loop_id=null.
 *  Rows inside loops have iter=[i] (one int per nesting level),
 *  loop_path=[loop_id] parallel to iter, parent_loop_id = innermost
 *  enclosing loop. Loop nodes themselves are not emitted. */
export interface NodeListEntry {
  node_id: string;
  /** Human-readable label from the workflow's optional `name:` field.
   *  When null, fall back to `node_id`. */
  name: string | null;
  kind: NodeKind | null;
  actor: NodeActor | null;
  state: NodeRunState;
  attempts: number;
  last_error: string | null;
  started_at: string | null;
  finished_at: string | null;
  iter: number[];
  loop_path: string[];
  parent_loop_id: string | null;
}

export interface JobDetail {
  job_slug: string;
  workflow_name: string;
  workflow_path: string;
  state: JobState;
  submitted_at: string;
  updated_at: string;
  repo_slug: string | null;
  nodes: NodeListEntry[];
  /** Loop `id → name` map for every loop with a `name:` set. Frontend
   *  uses this to label section headers (loop nodes are not emitted as
   *  rows). Loops without a name are absent; fall back to the loop_id. */
  loop_names: Record<string, string>;
}

export interface NodeDetail {
  node_id: string;
  state: NodeRunState;
  attempts: number;
  last_error: string | null;
  started_at: string | null;
  finished_at: string | null;
  outputs: Record<string, EnvelopePayload>;
}

/** Response from `GET /api/jobs/{slug}/nodes/{id}/chat`. `turns` is the
 *  raw stream-json output emitted by claude (`type: system|assistant|
 *  user|result`). `has_chat=false` means no `chat.jsonl` on disk — old
 *  jobs and not-yet-run nodes both surface this way; the frontend
 *  renders "no transcript" in either case. */
export interface AgentChatResponse {
  turns: Record<string, unknown>[];
  attempt: number;
  has_chat: boolean;
}

export interface EnvelopePayload {
  type: string;
  version: string;
  repo: string | null;
  producer_node: string;
  produced_at: string;
  value: unknown;
}

// ── HIL ──────────────────────────────────────────────────────────────────

export interface HilQueueItem {
  kind: HilKind;
  job_slug: string;
  workflow_name: string;
  node_id: string;
  iter: number[];
  created_at: string | null;
  // explicit-only
  output_var_names: string[];
  output_types: Record<string, string>;
  presentation: Record<string, unknown>;
  /** Per-output-var form schema: list of `[field_name, widget_type]`. */
  form_schemas: Record<string, [string, string][]>;
  // implicit-only
  call_id: string | null;
  question: string | null;
}

export interface HilAnswerRequest {
  var_name: string;
  value: Record<string, unknown>;
}

export interface AskAnswerRequest {
  answer: string;
}

// ── Submit ───────────────────────────────────────────────────────────────

export interface JobSubmitRequest {
  project_slug: string;
  job_type: string;
  title: string;
  request_text: string;
  dry_run: boolean;
}

export interface JobSubmitResponse {
  job_slug: string;
  dry_run: boolean;
  stages?: unknown[];
}

// ── Projects ─────────────────────────────────────────────────────────────

export type HealthCheckStatus = "pass" | "warn" | "fail";

export interface ProjectListItem {
  slug: string;
  name: string;
  repo_path: string;
  remote_url: string | null;
  default_branch: string | null;
  open_jobs: number;
  last_job_at: string | null;
  last_health_check_at: string | null;
  last_health_check_status: HealthCheckStatus | null;
}

export interface ProjectDetail {
  slug: string;
  name: string;
  repo_path: string;
  remote_url: string | null;
  default_branch: string;
  last_health_check_at: string | null;
  last_health_check_status: HealthCheckStatus | null;
}

export interface RegisterProjectRequest {
  path: string;
  slug?: string;
  name?: string;
}

export interface VerifyResult {
  status: HealthCheckStatus;
  remote_url: string | null;
  default_branch: string | null;
  reason: string | null;
}

export interface RegisterProjectResponse {
  project: ProjectDetail;
  verify: VerifyResult;
}

// ── Workflows (bundled) ──────────────────────────────────────────────────

export interface WorkflowListItem {
  job_type: string;
  workflow_name: string;
}

/** GET /api/projects/{slug}/workflows — Stage 5. Union of bundled +
 *  project-local workflows, with origin labelling and per-entry
 *  validation status. The dashboard hides invalid entries from the
 *  submit dropdown but lists them so the operator can fix them. */
export interface ProjectWorkflowItem {
  job_type: string;
  workflow_name: string | null;
  source: "bundled" | "custom";
  valid: boolean;
  error: string | null;
}

/** Body of POST /api/projects/{slug}/workflows/copy — Stage 6. */
export interface CopyWorkflowRequest {
  source: string;
  dest_name?: string;
}

export interface CopyWorkflowResponse {
  destination: string;
  workflow: ProjectWorkflowItem;
}

// ── Settings ─────────────────────────────────────────────────────────────

export interface SettingsResponse {
  runner_mode: string;
  claude_binary: string | null;
  root: string;
}

// ── Health ───────────────────────────────────────────────────────────────

export interface HealthResponse {
  ok: boolean;
}

// ── SSE ──────────────────────────────────────────────────────────────────
//
// Replay events (from events.jsonl) carry ``seq`` and standard event
// fields. Live events (PathChange notifications) carry ``change_kind``.
// Discriminate via ``"seq" in event``.

export type SseScope = "global" | `job/${string}` | `node/${string}/${string}`;

export interface ReplaySseEvent {
  seq: number;
  timestamp: string;
  event_type: string;
  source: string;
  job_id: string;
  stage_id: string | null;
  payload: Record<string, unknown>;
}

export interface LiveSseEvent {
  scope: string;
  change_kind: "added" | "modified" | "deleted";
  file_kind:
    | "project"
    | "job"
    | "node"
    | "variable"
    | "loop_variable"
    | "pending"
    | "ask"
    | "events_jsonl"
    | "unknown"
    | string;
  job_slug?: string;
  node_id?: string;
  var_name?: string;
  loop_id?: string;
  iteration?: number;
  project_slug?: string;
  call_id?: string;
}

export type SseEvent = ReplaySseEvent | LiveSseEvent;
