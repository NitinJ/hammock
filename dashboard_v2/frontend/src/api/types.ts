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

export interface WorkflowSummary {
  name: string;
  description: string | null;
  nodes: Array<{
    id: string;
    prompt: string;
    after: string[];
    human_review: boolean;
    description: string | null;
  }>;
}

export interface ChatTurn {
  type: string;
  [k: string]: unknown;
}

export interface ChatResponse {
  turns: ChatTurn[];
  has_chat: boolean;
}
