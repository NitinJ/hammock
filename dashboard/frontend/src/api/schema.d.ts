/**
 * API types for the Hammock dashboard.
 * Hand-authored to match FastAPI's /openapi.json (run `pnpm schema:sync` to regenerate).
 * Updated in Stage 12 to fix divergences from backend projections.py.
 */

// ── State enums ────────────────────────────────────────────────────────────

export type JobState =
  | "SUBMITTED"
  | "STAGES_RUNNING"
  | "BLOCKED_ON_HUMAN"
  | "COMPLETED"
  | "ABANDONED"
  | "FAILED";

export type StageState =
  | "PENDING"
  | "READY"
  | "RUNNING"
  | "PARTIALLY_BLOCKED"
  | "BLOCKED_ON_HUMAN"
  | "ATTENTION_NEEDED"
  | "WRAPPING_UP"
  | "SUCCEEDED"
  | "FAILED"
  | "CANCELLED";

export type TaskState = "RUNNING" | "BLOCKED_ON_HUMAN" | "STUCK" | "DONE" | "FAILED" | "CANCELLED";

export type HilState = "AWAITING" | "ANSWERED" | "CANCELLED";

export type HilKind = "ask" | "review" | "manual-step";

export type DoctorStatus = "pass" | "warn" | "fail" | "unknown";

// ── Core / persisted models ────────────────────────────────────────────────

export interface ProjectConfig {
  slug: string;
  name: string;
  repo_path: string;
  remote_url: string | null;
  default_branch: string;
  created_at: string;
  last_health_check_at: string | null;
  last_health_check_status: "pass" | "warn" | "fail" | null;
}

/** v0 alias — ProjectConfig and Project are the same shape. */
export type Project = ProjectConfig;

export interface JobConfig {
  job_id: string;
  job_slug: string;
  project_slug: string;
  job_type: "build-feature" | "fix-bug" | string;
  created_at: string;
  created_by: string;
  state: JobState;
}

export interface StageRun {
  stage_id: string;
  job_slug: string;
  state: StageState;
  started_at: string | null;
  completed_at: string | null;
  cost_usd: number;
  restart_count: number;
}

// ── HIL models (full, for Stage 13 form renderer) ─────────────────────────

export interface HilItem {
  item_id: string;
  job_slug: string;
  stage_id: string;
  kind: HilKind;
  state: HilState;
  created_at: string;
  answered_at: string | null;
  question: AskQuestion | ReviewQuestion | ManualStepQuestion;
  answer: AskAnswer | ReviewAnswer | ManualStepAnswer | null;
}

export interface AskQuestion {
  kind: "ask";
  prompt: string;
  context: string | null;
}

export interface ReviewQuestion {
  kind: "review";
  prompt: string;
  artifact_path: string | null;
  context: string | null;
}

export interface ManualStepQuestion {
  kind: "manual-step";
  instructions: string;
  context: string | null;
}

export interface AskAnswer {
  kind: "ask";
  answer: string;
}

export interface ReviewAnswer {
  kind: "review";
  approved: boolean;
  comments: string | null;
}

export interface ManualStepAnswer {
  kind: "manual-step";
  completed: boolean;
  notes: string | null;
}

export interface TaskRecord {
  task_id: string;
  stage_id: string;
  state: TaskState;
  created_at: string;
  started_at?: string | null;
  ended_at?: string | null;
  subagent_id?: string | null;
  cost_accrued?: number;
  restart_count?: number;
}

// ── Projection types (matching dashboard/state/projections.py) ─────────────

export interface ProjectListItem {
  slug: string;
  name: string;
  repo_path: string;
  default_branch: string;
  total_jobs: number;
  open_hil_count: number;
  last_job_at: string | null;
  doctor_status: DoctorStatus;
}

export interface ProjectDetail {
  project: ProjectConfig;
  total_jobs: number;
  open_hil_count: number;
  jobs_by_state: Record<string, number>;
}

export interface JobListItem {
  job_id: string;
  job_slug: string;
  project_slug: string;
  job_type: string;
  state: JobState;
  created_at: string;
  total_cost_usd: number;
  current_stage_id: string | null;
}

export interface StageListEntry {
  stage_id: string;
  state: StageState;
  attempt: number;
  started_at: string | null;
  ended_at: string | null;
  cost_accrued: number;
}

export interface JobDetail {
  job: JobConfig;
  stages: StageListEntry[];
  total_cost_usd: number;
}

export interface StageDetail {
  job_slug: string;
  stage: StageRun;
  tasks: TaskRecord[];
}

export interface StageRun {
  stage_id: string;
  attempt: number;
  state: StageState;
  started_at: string | null;
  ended_at: string | null;
  cost_accrued: number;
  restart_count: number;
}

export interface ActiveStageStripItem {
  project_slug: string;
  job_slug: string;
  stage_id: string;
  state: StageState;
  cost_accrued: number;
  started_at: string | null;
}

export interface HilQueueItem {
  item_id: string;
  kind: "ask" | "review" | "manual-step";
  status: "awaiting" | "answered" | "cancelled";
  stage_id: string;
  job_slug: string;
  project_slug: string | null;
  created_at: string;
  age_seconds: number;
}

export interface CostRollup {
  scope: "project" | "job" | "stage";
  id: string;
  total_usd: number;
  total_tokens: number;
  by_stage: Record<string, number>;
  by_agent: Record<string, number>;
}

export interface SystemHealth {
  cache_size: Record<string, number>;
  watcher_alive: boolean;
  mcp_server_count: number;
  drivers_alive: number;
}

export interface ObservatoryMetrics {
  [key: string]: unknown;
}

// ── API responses ──────────────────────────────────────────────────────────

export interface HealthResponse {
  ok: boolean;
  cache_size: number;
}

// ── SSE events ─────────────────────────────────────────────────────────────
//
// Stage 12.5 (A4): live and replay messages are distinct shapes.
//
// Replay events (from events.jsonl) are *unnamed* SSE messages fired via
// ``EventSource.onmessage``.  They carry ``seq`` for Last-Event-ID tracking.
//
// Live events (CacheChange notifications from the watcher) are also *unnamed*
// (no ``event:`` line) — the A4 fix dropped the ``event: {kind}_changed`` line
// so the browser fires ``onmessage`` for them too.  They carry ``change_kind``
// instead of ``seq``.
//
// Narrow the union before accessing kind-specific fields.

/** Replay event — emitted by the server from on-disk events.jsonl. */
export interface ReplaySseEvent {
  seq: number;
  timestamp: string;
  event_type: string;
  source: "job_driver" | "agent0" | "subagent" | "dashboard" | "engine" | "human" | "hook";
  job_id: string;
  stage_id: string | null;
  task_id: string | null;
  subagent_id: string | null;
  parent_event_seq: number | null;
  payload: Record<string, unknown>;
}

/** Live event — emitted by the server when a state file changes (CacheChange). */
export interface LiveSseEvent {
  scope: string;
  change_kind: "added" | "modified" | "deleted";
  file_kind: "project" | "job" | "stage" | "hil" | string;
  job_slug?: string;
  stage_id?: string;
  project_slug?: string;
  hil_id?: string;
}

/** Discriminated union over both SSE event shapes. Narrow on ``"seq" in event``. */
export type SseEvent = ReplaySseEvent | LiveSseEvent;
