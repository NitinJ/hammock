/** Type-only mirrors of dashboard_v2/api projection shapes. */

export type JobState = "submitted" | "running" | "blocked_on_human" | "completed" | "failed";
export type NodeState = "pending" | "running" | "succeeded" | "failed";

export interface NodeOverview {
  id: string;
  state: NodeState;
  started_at: string | null;
  finished_at: string | null;
  awaiting_human: boolean;
}

export interface JobSummary {
  slug: string;
  workflow_name: string;
  state: JobState;
  submitted_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  request: string;
  nodes: NodeOverview[];
}

export interface HumanDecision {
  decision: "approved" | "needs-revision";
  comment: string | null;
}

export interface NodeDetail {
  id: string;
  state: NodeState;
  started_at: string | null;
  finished_at: string | null;
  input: string;
  prompt: string;
  output: string;
  awaiting_human: boolean;
  human_decision: HumanDecision | null;
}

export interface WorkflowNode {
  id: string;
  prompt: string;
  after: string[];
  human_review: boolean;
  description: string | null;
  requires?: string[];
}

export interface WorkflowSummary {
  name: string;
  description: string | null;
  nodes: WorkflowNode[];
  /** "bundled", "custom", or a project slug. */
  source: string;
  /** Back-compat alias for source === "bundled". */
  bundled?: boolean;
  node_count?: number;
  modified_at?: string | null;
}

export interface WorkflowDetail extends WorkflowSummary {
  yaml: string;
  bundled: boolean;
}

export interface ChatTurn {
  type: string;
  [k: string]: unknown;
}

export interface ChatResponse {
  turns: ChatTurn[];
  has_chat: boolean;
}

export interface ProjectHealth {
  path_exists: boolean;
  is_git_repo: boolean;
  default_branch: string | null;
}

export interface Project {
  slug: string;
  name: string;
  repo_path: string;
  registered_at: string;
  default_branch: string | null;
  health: ProjectHealth;
}

export interface ProjectPrompt {
  name: string;
  bundled: boolean;
}

/** Aggregate prompt entry (across bundled + every project). */
export interface PromptEntry {
  name: string;
  /** "bundled" or a project slug. */
  source: string;
  path: string;
  size: number;
  modified_at: string;
}

export interface PromptDetail {
  name: string;
  source: string;
  content: string;
}

export interface OrchestratorMessage {
  id: string;
  from: "operator" | "orchestrator";
  timestamp: string;
  text: string;
}
