/**
 * vue-query bindings for the v1 dashboard API.
 *
 * URL surface (mirrors `dashboard/api/*.py`):
 *
 *   GET  /api/health
 *   GET  /api/jobs                    — list (?repo_slug, ?state)
 *   GET  /api/jobs/{slug}             — detail with NodeListEntry list
 *   POST /api/jobs                    — submit (returns job_slug)
 *   GET  /api/jobs/{slug}/nodes/{id}  — node detail (envelopes)
 *   GET  /api/hil                     — all pending HIL across jobs
 *   GET  /api/hil/{slug}              — pending for one job
 *   GET  /api/hil/{slug}/{node}       — explicit pending detail
 *   POST /api/hil/{slug}/{node}/answer
 *   GET  /api/hil/{slug}/asks/{call_id}
 *   POST /api/hil/{slug}/asks/{call_id}/answer
 *   GET  /api/settings
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/vue-query";
import { computed, toValue } from "vue";
import type { MaybeRefOrGetter } from "vue";
import { api } from "./client";
import type {
  AgentChatResponse,
  AskAnswerRequest,
  HealthResponse,
  HilAnswerRequest,
  HilQueueItem,
  JobDetail,
  JobListItem,
  JobState,
  JobSubmitRequest,
  JobSubmitResponse,
  NodeDetail,
  CopyWorkflowRequest,
  CopyWorkflowResponse,
  ProjectDetail,
  ProjectListItem,
  ProjectWorkflowItem,
  RegisterProjectRequest,
  RegisterProjectResponse,
  SettingsResponse,
  WorkflowListItem,
} from "./schema.d";

export const QUERY_KEYS = {
  health: ["health"] as const,
  projects: ["projects"] as const,
  project: (slug: string) => ["projects", slug] as const,
  workflows: ["workflows"] as const,
  jobs: (repoSlug?: string | null, state?: JobState | null) =>
    ["jobs", "list", repoSlug ?? null, state ?? null] as const,
  job: (jobSlug: string) => ["jobs", "detail", jobSlug] as const,
  node: (jobSlug: string, nodeId: string) => ["jobs", jobSlug, "nodes", nodeId] as const,
  agentChat: (jobSlug: string, nodeId: string, attempt: number) =>
    ["jobs", jobSlug, "nodes", nodeId, "chat", attempt] as const,
  hil: (jobSlug?: string | null) => ["hil", jobSlug ?? "all"] as const,
  settings: ["settings"] as const,
};

export function useWorkflows() {
  return useQuery({
    queryKey: QUERY_KEYS.workflows,
    queryFn: () => api.get<WorkflowListItem[]>("/workflows"),
  });
}

/** Stage 5 — per-project workflow listing (bundled + project-local). */
export function useProjectWorkflows(slug: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => ["projects", toValue(slug), "workflows"] as const),
    queryFn: () => api.get<ProjectWorkflowItem[]>(`/projects/${toValue(slug)}/workflows`),
    enabled: computed(() => Boolean(toValue(slug))),
  });
}

export function useProjects() {
  return useQuery({
    queryKey: QUERY_KEYS.projects,
    queryFn: () => api.get<ProjectListItem[]>("/projects"),
  });
}

export function useProject(slug: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => QUERY_KEYS.project(toValue(slug))),
    queryFn: () => api.get<ProjectDetail>(`/projects/${toValue(slug)}`),
    enabled: computed(() => Boolean(toValue(slug))),
  });
}

export function useRegisterProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RegisterProjectRequest) =>
      api.post<RegisterProjectResponse>("/projects", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => api.del<void>(`/projects/${slug}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

export function useReverifyProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => api.post<RegisterProjectResponse>(`/projects/${slug}/verify`, {}),
    onSuccess: (_data, slug) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["projects", slug] });
    },
  });
}

/** Stage 6 — fork a bundled workflow into the project's repo. */
export function useCopyWorkflow(projectSlug: MaybeRefOrGetter<string>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CopyWorkflowRequest) =>
      api.post<CopyWorkflowResponse>(`/projects/${toValue(projectSlug)}/workflows/copy`, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["projects", toValue(projectSlug), "workflows"] });
    },
  });
}

export function useHealth() {
  return useQuery({
    queryKey: QUERY_KEYS.health,
    queryFn: () => api.get<HealthResponse>("/health"),
  });
}

export function useJobs(
  repoSlug?: MaybeRefOrGetter<string | null | undefined>,
  state?: MaybeRefOrGetter<JobState | null | undefined>,
) {
  return useQuery({
    queryKey: computed(() => QUERY_KEYS.jobs(toValue(repoSlug) ?? null, toValue(state) ?? null)),
    queryFn: () => {
      const r = toValue(repoSlug);
      const s = toValue(state);
      const params = new URLSearchParams();
      if (r) params.set("repo_slug", r);
      if (s) params.set("state", s);
      const qs = params.toString();
      return api.get<JobListItem[]>(qs ? `/jobs?${qs}` : "/jobs");
    },
  });
}

export function useJob(jobSlug: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => QUERY_KEYS.job(toValue(jobSlug))),
    queryFn: () => api.get<JobDetail>(`/jobs/${toValue(jobSlug)}`),
    enabled: computed(() => Boolean(toValue(jobSlug))),
  });
}

export function useNodeDetail(
  jobSlug: MaybeRefOrGetter<string>,
  nodeId: MaybeRefOrGetter<string | null | undefined>,
) {
  return useQuery({
    queryKey: computed(() => QUERY_KEYS.node(toValue(jobSlug), toValue(nodeId) ?? "")),
    queryFn: () => api.get<NodeDetail>(`/jobs/${toValue(jobSlug)}/nodes/${toValue(nodeId)}`),
    enabled: computed(() => Boolean(toValue(jobSlug)) && Boolean(toValue(nodeId))),
    // 404 is the common failure mode (node not dispatched yet). Don't
    // retry — the JobOverview surface treats 404 as "not started".
    retry: false,
  });
}

export function useAgentChat(
  jobSlug: MaybeRefOrGetter<string>,
  nodeId: MaybeRefOrGetter<string | null | undefined>,
  attempt?: MaybeRefOrGetter<number | null | undefined>,
) {
  return useQuery({
    queryKey: computed(() =>
      QUERY_KEYS.agentChat(toValue(jobSlug), toValue(nodeId) ?? "", toValue(attempt) ?? 1),
    ),
    queryFn: () => {
      const a = toValue(attempt) ?? 1;
      return api.get<AgentChatResponse>(
        `/jobs/${toValue(jobSlug)}/nodes/${toValue(nodeId)}/chat?attempt=${a}`,
      );
    },
    enabled: computed(() => Boolean(toValue(jobSlug)) && Boolean(toValue(nodeId))),
    retry: false,
  });
}

export function useHilQueue(jobSlug?: MaybeRefOrGetter<string | null | undefined>) {
  return useQuery({
    queryKey: computed(() => QUERY_KEYS.hil(toValue(jobSlug) ?? null)),
    queryFn: () => {
      const slug = toValue(jobSlug);
      return api.get<HilQueueItem[]>(slug ? `/hil/${slug}` : "/hil");
    },
  });
}

export function useSettings() {
  return useQuery({
    queryKey: QUERY_KEYS.settings,
    queryFn: () => api.get<SettingsResponse>("/settings"),
  });
}

// ── Mutations ──────────────────────────────────────────────────────────

export function useSubmitJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: JobSubmitRequest) => api.post<JobSubmitResponse>("/jobs", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
}

export function useAnswerExplicitHil(jobSlug: MaybeRefOrGetter<string>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { node_id: string; body: HilAnswerRequest }) =>
      api.post(`/hil/${toValue(jobSlug)}/${vars.node_id}/answer`, vars.body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hil"] });
      qc.invalidateQueries({ queryKey: ["jobs", "detail", toValue(jobSlug)] });
    },
  });
}

export function useAnswerImplicitHil(jobSlug: MaybeRefOrGetter<string>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (vars: { call_id: string; body: AskAnswerRequest }) =>
      api.post(`/hil/${toValue(jobSlug)}/asks/${vars.call_id}/answer`, vars.body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["hil"] });
    },
  });
}
