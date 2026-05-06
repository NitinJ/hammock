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
  ProjectDetail,
  ProjectListItem,
  SettingsResponse,
} from "./schema.d";

export const QUERY_KEYS = {
  health: ["health"] as const,
  projects: ["projects"] as const,
  project: (slug: string) => ["projects", slug] as const,
  jobs: (repoSlug?: string | null, state?: JobState | null) =>
    ["jobs", "list", repoSlug ?? null, state ?? null] as const,
  job: (jobSlug: string) => ["jobs", "detail", jobSlug] as const,
  node: (jobSlug: string, nodeId: string) => ["jobs", jobSlug, "nodes", nodeId] as const,
  hil: (jobSlug?: string | null) => ["hil", jobSlug ?? "all"] as const,
  settings: ["settings"] as const,
};

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
