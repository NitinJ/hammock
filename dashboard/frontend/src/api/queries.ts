import { useQuery } from "@tanstack/vue-query";
import type { MaybeRefOrGetter } from "vue";
import { api } from "./client";
import type { HealthResponse, ProjectListItem, JobListItem, HilItem } from "./schema.d";

export const QUERY_KEYS = {
  health: ["health"] as const,
  projects: ["projects"] as const,
  project: (slug: string) => ["projects", slug] as const,
  jobs: (projectSlug?: string) => ["jobs", projectSlug ?? null] as const,
  job: (jobSlug: string) => ["jobs", jobSlug] as const,
  stages: (jobSlug: string) => ["stages", jobSlug] as const,
  stage: (jobSlug: string, stageId: string) => ["stages", jobSlug, stageId] as const,
  hil: (status?: string) => ["hil", status ?? "all"] as const,
  hilItem: (itemId: string) => ["hil", itemId] as const,
  costs: (scope: string, id: string) => ["costs", scope, id] as const,
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

export function useJobs(projectSlug?: MaybeRefOrGetter<string | undefined>) {
  return useQuery({
    queryKey: QUERY_KEYS.jobs(),
    queryFn: () => api.get<JobListItem[]>("/jobs"),
  });
}

export function useHilQueue(status: MaybeRefOrGetter<string> = "awaiting") {
  return useQuery({
    queryKey: QUERY_KEYS.hil("awaiting"),
    queryFn: () => api.get<HilItem[]>("/hil?status=awaiting"),
  });
}
