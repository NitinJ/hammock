import { useQuery } from "@tanstack/vue-query";
import { computed, toValue } from "vue";
import type { MaybeRefOrGetter } from "vue";
import { api } from "./client";
import type {
  ActiveStageStripItem,
  CostRollup,
  HealthResponse,
  HilQueueItem,
  JobDetail,
  JobListItem,
  ProjectDetail,
  ProjectListItem,
} from "./schema.d";

export const QUERY_KEYS = {
  health: ["health"] as const,
  projects: ["projects"] as const,
  project: (slug: string) => ["projects", slug] as const,
  jobs: (projectSlug?: string | null) => ["jobs", "list", projectSlug ?? null] as const,
  job: (jobSlug: string) => ["jobs", "detail", jobSlug] as const,
  stages: (jobSlug: string) => ["stages", jobSlug] as const,
  stage: (jobSlug: string, stageId: string) => ["stages", jobSlug, stageId] as const,
  hil: (status?: string) => ["hil", status ?? "all"] as const,
  hilItem: (itemId: string) => ["hil", itemId] as const,
  costs: (scope: string, id: string, job?: string | null) =>
    ["costs", scope, id, job ?? null] as const,
  activeStages: ["active-stages"] as const,
  artifact: (jobSlug: string, path: string) => ["artifact", jobSlug, path] as const,
};

export function useHealth() {
  return useQuery({
    queryKey: QUERY_KEYS.health,
    queryFn: () => api.get<HealthResponse>("/health"),
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
  });
}

export function useJobs(projectSlug?: MaybeRefOrGetter<string | undefined | null>) {
  return useQuery({
    queryKey: computed(() => QUERY_KEYS.jobs(toValue(projectSlug) ?? null)),
    queryFn: () => {
      const slug = toValue(projectSlug);
      return api.get<JobListItem[]>(slug ? `/jobs?project=${slug}` : "/jobs");
    },
  });
}

export function useJob(jobSlug: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => QUERY_KEYS.job(toValue(jobSlug))),
    queryFn: () => api.get<JobDetail>(`/jobs/${toValue(jobSlug)}`),
  });
}

export function useActiveStages() {
  return useQuery({
    queryKey: QUERY_KEYS.activeStages,
    queryFn: () => api.get<ActiveStageStripItem[]>("/active-stages"),
  });
}

export function useHilQueue(status: MaybeRefOrGetter<string> = "awaiting") {
  return useQuery({
    queryKey: computed(() => QUERY_KEYS.hil(toValue(status))),
    queryFn: () => api.get<HilQueueItem[]>(`/hil?status=${toValue(status)}`),
  });
}

export function useCosts(
  scope: MaybeRefOrGetter<string>,
  id: MaybeRefOrGetter<string>,
  job?: MaybeRefOrGetter<string | null | undefined>,
) {
  return useQuery({
    queryKey: computed(() => QUERY_KEYS.costs(toValue(scope), toValue(id), toValue(job) ?? null)),
    queryFn: () => {
      const jobVal = toValue(job);
      const jobParam = jobVal ? `&job=${encodeURIComponent(jobVal)}` : "";
      return api.get<CostRollup>(`/costs?scope=${toValue(scope)}&id=${toValue(id)}${jobParam}`);
    },
    enabled: computed(() => {
      const idOk = Boolean(toValue(id));
      const isStage = toValue(scope) === "stage";
      return idOk && (!isStage || Boolean(toValue(job)));
    }),
  });
}

export function useArtifact(jobSlug: MaybeRefOrGetter<string>, path: MaybeRefOrGetter<string>) {
  return useQuery({
    queryKey: computed(() => QUERY_KEYS.artifact(toValue(jobSlug), toValue(path))),
    queryFn: async () => {
      const res = await fetch(`/api/artifacts/${toValue(jobSlug)}/${toValue(path)}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}: artifact not found`);
      return res.text();
    },
    enabled: computed(() => Boolean(toValue(jobSlug)) && Boolean(toValue(path))),
  });
}
