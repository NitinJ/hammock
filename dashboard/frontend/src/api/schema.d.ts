/**
 * Hand-mocked OpenAPI types for Stage 11.
 * Generated from FastAPI's /openapi.json once Stage 9 lands.
 * Run: pnpm schema:sync
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
  | "RUNNING"
  | "ATTENTION_NEEDED"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "SKIPPED";

export type TaskState = "OPEN" | "IN_PROGRESS" | "DONE" | "FAILED" | "CANCELLED";

export type HilState = "AWAITING" | "ANSWERED" | "CANCELLED";

export type HilKind = "ask" | "review" | "manual-step";

// ── Core models ────────────────────────────────────────────────────────────

export interface Project {
  slug: string;
  name: string;
  repo_path: string;
  github_remote: string | null;
  created_at: string;
}

export interface Job {
  job_slug: string;
  project_slug: string;
  title: string;
  job_type: "build-feature" | "fix-bug";
  state: JobState;
  created_at: string;
  completed_at: string | null;
  budget_cap_usd: number | null;
  cost_usd: number;
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

// ── API response shapes ────────────────────────────────────────────────────

export interface HealthResponse {
  ok: boolean;
  cache_size: number;
}

export interface ProjectListItem {
  slug: string;
  name: string;
  repo_path: string;
  last_job_at: string | null;
  active_job_count: number;
  open_hil_count: number;
  doctor_status: "green" | "yellow" | "red";
  cost_30d_usd: number;
}

export interface JobListItem {
  job_slug: string;
  project_slug: string;
  title: string;
  job_type: string;
  state: JobState;
  created_at: string;
  cost_usd: number;
  budget_cap_usd: number | null;
}

export interface CostRollup {
  scope: "project" | "job" | "stage";
  id: string;
  total_usd: number;
  breakdown: CostBreakdownEntry[];
}

export interface CostBreakdownEntry {
  label: string;
  cost_usd: number;
}

// ── SSE event ──────────────────────────────────────────────────────────────

export interface SseEvent {
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
