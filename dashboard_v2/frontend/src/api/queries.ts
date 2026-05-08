import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, type Ref } from "vue";

import { api } from "./client";
import type {
  ChatResponse,
  JobSummary,
  NodeDetail,
  WorkflowDetail,
  WorkflowSummary,
} from "./types";

export const QUERY_KEYS = {
  workflows: () => ["workflows"] as const,
  workflow: (name: string) => ["workflows", name] as const,
  jobs: () => ["jobs"] as const,
  job: (slug: string) => ["jobs", slug] as const,
  node: (slug: string, nodeId: string) => ["jobs", slug, "nodes", nodeId] as const,
  chat: (slug: string, nodeId: string) => ["jobs", slug, "nodes", nodeId, "chat"] as const,
  orchestratorChat: (slug: string) => ["jobs", slug, "orchestrator", "chat"] as const,
};

export function useWorkflows() {
  return useQuery({
    queryKey: QUERY_KEYS.workflows(),
    queryFn: () => api.get<{ workflows: WorkflowSummary[] }>("/api/workflows"),
  });
}

export function useWorkflow(name: Ref<string>) {
  const queryKey = computed(() => QUERY_KEYS.workflow(name.value));
  return useQuery({
    queryKey,
    queryFn: () => api.get<WorkflowDetail>(`/api/workflows/${name.value}`),
    enabled: computed(() => !!name.value),
  });
}

export function useCreateWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; yaml: string }) =>
      api.post<{ name: string }>("/api/workflows", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.workflows() });
    },
  });
}

export function useUpdateWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, yaml }: { name: string; yaml: string }) =>
      api.put<{ name: string }>(`/api/workflows/${name}`, { yaml }),
    onSuccess: (_, { name }) => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.workflow(name) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.workflows() });
    },
  });
}

export function useDeleteWorkflow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.del<{ name: string }>(`/api/workflows/${name}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.workflows() });
    },
  });
}

export async function validateWorkflowYaml(
  yaml: string,
): Promise<{
  valid: boolean;
  error?: string;
  name?: string;
  description?: string | null;
  nodes?: import("./types").WorkflowNode[];
}> {
  return api.post("/api/workflows/validate", { yaml });
}

export function useJobs() {
  return useQuery({
    queryKey: QUERY_KEYS.jobs(),
    queryFn: async () => {
      const r = await api.get<{ jobs: JobSummary[] }>("/api/jobs");
      return r.jobs;
    },
    refetchInterval: 5000,
  });
}

export function useJob(slug: Ref<string>) {
  const queryKey = computed(() => QUERY_KEYS.job(slug.value));
  return useQuery({
    queryKey,
    queryFn: () => api.get<JobSummary>(`/api/jobs/${slug.value}`),
    enabled: computed(() => !!slug.value),
    refetchInterval: 2000,
  });
}

export function useNode(slug: Ref<string>, nodeId: Ref<string | null>) {
  const queryKey = computed(() => QUERY_KEYS.node(slug.value, nodeId.value ?? ""));
  return useQuery({
    queryKey,
    queryFn: () => api.get<NodeDetail>(`/api/jobs/${slug.value}/nodes/${nodeId.value}`),
    enabled: computed(() => !!slug.value && !!nodeId.value),
    refetchInterval: 2000,
  });
}

export function useNodeChat(slug: Ref<string>, nodeId: Ref<string | null>) {
  const queryKey = computed(() => QUERY_KEYS.chat(slug.value, nodeId.value ?? ""));
  return useQuery({
    queryKey,
    queryFn: () => api.get<ChatResponse>(`/api/jobs/${slug.value}/nodes/${nodeId.value}/chat`),
    enabled: computed(() => !!slug.value && !!nodeId.value),
    refetchInterval: 3000,
  });
}

export function useOrchestratorChat(slug: Ref<string>) {
  const queryKey = computed(() => QUERY_KEYS.orchestratorChat(slug.value));
  return useQuery({
    queryKey,
    queryFn: () => api.get<ChatResponse>(`/api/jobs/${slug.value}/orchestrator/chat`),
    enabled: computed(() => !!slug.value),
    refetchInterval: 3000,
  });
}

export function useSubmitJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { workflow: string; request: string; artifacts?: File[] }) => {
      if (body.artifacts && body.artifacts.length > 0) {
        const fd = new FormData();
        fd.append("workflow", body.workflow);
        fd.append("request", body.request);
        for (const f of body.artifacts) {
          fd.append("artifacts", f, f.name);
        }
        return api.postForm<{ slug: string; pid: number }>("/api/jobs", fd);
      }
      return api.post<{ slug: string; pid: number }>("/api/jobs", {
        workflow: body.workflow,
        request: body.request,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.jobs() });
    },
  });
}

export function useSubmitDecision(slug: Ref<string>, nodeId: Ref<string>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { decision: "approved" | "needs-revision"; comment?: string }) =>
      api.post(`/api/jobs/${slug.value}/nodes/${nodeId.value}/human_decision`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.node(slug.value, nodeId.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.job(slug.value) });
    },
  });
}
