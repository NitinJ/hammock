import { useMutation, useQuery, useQueryClient } from "@tanstack/vue-query";
import { computed, type Ref } from "vue";

import { api } from "./client";
import type {
  ChatResponse,
  JobSummary,
  NodeDetail,
  OrchestratorMessage,
  Project,
  ProjectPrompt,
  PromptDetail,
  PromptEntry,
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
  orchestratorMessages: (slug: string) => ["jobs", slug, "orchestrator", "messages"] as const,
  orchestratorEvents: (slug: string) => ["jobs", slug, "orchestrator", "events"] as const,
  projects: () => ["projects"] as const,
  project: (slug: string) => ["projects", slug] as const,
  projectWorkflows: (slug: string) => ["projects", slug, "workflows"] as const,
  projectPrompts: (slug: string) => ["projects", slug, "prompts"] as const,
  projectPrompt: (slug: string, name: string) => ["projects", slug, "prompts", name] as const,
  prompts: (source?: string | null) => ["prompts", source ?? "all"] as const,
  promptDetail: (source: string, name: string) => ["prompts", source, name] as const,
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

export async function validateWorkflowYaml(yaml: string): Promise<{
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

interface OrchestratorEvent {
  kind: string;
  at: string;
  detail: string;
  node_id?: string;
}

export function useOrchestratorEvents(slug: Ref<string>) {
  // SSE invalidates this on node_state_changed / job_state_changed /
  // awaiting_human / human_decision_received. The 5s safety-net
  // refetchInterval covers SSE drops; primary path is event-driven.
  const queryKey = computed(() => QUERY_KEYS.orchestratorEvents(slug.value));
  return useQuery({
    queryKey,
    queryFn: () =>
      api.get<{ events: OrchestratorEvent[] }>(`/api/jobs/${slug.value}/orchestrator/events`),
    enabled: computed(() => !!slug.value),
    refetchInterval: 5000,
  });
}

export function useOrchestratorMessages(slug: Ref<string>) {
  // SSE invalidates this on orchestrator_message_appended. Same
  // safety-net polling as events.
  const queryKey = computed(() => QUERY_KEYS.orchestratorMessages(slug.value));
  return useQuery({
    queryKey,
    queryFn: () =>
      api.get<{ messages: OrchestratorMessage[] }>(`/api/jobs/${slug.value}/orchestrator/messages`),
    enabled: computed(() => !!slug.value),
    refetchInterval: 5000,
  });
}

export function useSendOrchestratorMessage(slug: Ref<string>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { text: string }) =>
      api.post<{ ok: string }>(`/api/jobs/${slug.value}/orchestrator/messages`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.orchestratorMessages(slug.value) });
    },
  });
}

export function useSubmitJob() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      workflow: string;
      request: string;
      project_slug?: string;
      artifacts?: File[];
    }) => {
      if (body.artifacts && body.artifacts.length > 0) {
        const fd = new FormData();
        fd.append("workflow", body.workflow);
        fd.append("request", body.request);
        if (body.project_slug) fd.append("project_slug", body.project_slug);
        for (const f of body.artifacts) {
          fd.append("artifacts", f, f.name);
        }
        return api.postForm<{ slug: string; pid: number }>("/api/jobs", fd);
      }
      const payload: Record<string, string> = {
        workflow: body.workflow,
        request: body.request,
      };
      if (body.project_slug) payload.project_slug = body.project_slug;
      return api.post<{ slug: string; pid: number }>("/api/jobs", payload);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.jobs() });
    },
  });
}

export function useProjects() {
  return useQuery({
    queryKey: QUERY_KEYS.projects(),
    queryFn: async () => {
      const r = await api.get<{ projects: Project[] }>("/api/projects");
      return r.projects;
    },
  });
}

export function useProject(slug: Ref<string>) {
  const queryKey = computed(() => QUERY_KEYS.project(slug.value));
  return useQuery({
    queryKey,
    queryFn: () => api.get<Project>(`/api/projects/${slug.value}`),
    enabled: computed(() => !!slug.value),
  });
}

export function useRegisterProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { repo_path: string; slug?: string; name?: string }) =>
      api.post<Project>("/api/projects", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.projects() });
    },
  });
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => api.del<{ slug: string }>(`/api/projects/${slug}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.projects() });
    },
  });
}

export function useVerifyProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => api.post<Project>(`/api/projects/${slug}/verify`, {}),
    onSuccess: (_, slug) => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.project(slug) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.projects() });
    },
  });
}

export function useProjectWorkflows(slug: Ref<string>) {
  const queryKey = computed(() => QUERY_KEYS.projectWorkflows(slug.value));
  return useQuery({
    queryKey,
    queryFn: () =>
      api.get<{ workflows: WorkflowSummary[] }>(`/api/projects/${slug.value}/workflows`),
    enabled: computed(() => !!slug.value),
  });
}

export function useProjectPrompts(slug: Ref<string>) {
  const queryKey = computed(() => QUERY_KEYS.projectPrompts(slug.value));
  return useQuery({
    queryKey,
    queryFn: () => api.get<{ prompts: ProjectPrompt[] }>(`/api/projects/${slug.value}/prompts`),
    enabled: computed(() => !!slug.value),
  });
}

export function useProjectPrompt(slug: Ref<string>, name: Ref<string | null>) {
  const queryKey = computed(() => QUERY_KEYS.projectPrompt(slug.value, name.value ?? ""));
  return useQuery({
    queryKey,
    queryFn: () =>
      api.get<{ name: string; content: string; bundled: boolean }>(
        `/api/projects/${slug.value}/prompts/${name.value}`,
      ),
    enabled: computed(() => !!slug.value && !!name.value),
  });
}

export function useSaveProjectPrompt(slug: Ref<string>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; content: string }) =>
      api.post(`/api/projects/${slug.value}/prompts`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.projectPrompts(slug.value) });
    },
  });
}

export function useSaveProjectWorkflow(slug: Ref<string>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; yaml: string }) =>
      api.post(`/api/projects/${slug.value}/workflows`, body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.projectWorkflows(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.workflows() });
    },
  });
}

export function useUpdateProjectWorkflow(slug: Ref<string>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, yaml }: { name: string; yaml: string }) =>
      api.put(`/api/projects/${slug.value}/workflows/${name}`, { yaml }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.projectWorkflows(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.workflows() });
    },
  });
}

export function useDeleteProjectWorkflow(slug: Ref<string>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.del(`/api/projects/${slug.value}/workflows/${name}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.projectWorkflows(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.workflows() });
    },
  });
}

/** Aggregate prompts list across bundled + every registered project. */
export function usePrompts(source?: Ref<string | null>) {
  const queryKey = computed(() => QUERY_KEYS.prompts(source?.value));
  return useQuery({
    queryKey,
    queryFn: () => {
      const q = source?.value ? `?source=${encodeURIComponent(source.value)}` : "";
      return api.get<{ prompts: PromptEntry[] }>(`/api/prompts${q}`);
    },
  });
}

/** Detail (content) for any source. Bundled uses /api/prompts/bundled/{name};
 *  per-project uses /api/projects/{slug}/prompts/{name}. */
export function usePromptDetail(source: Ref<string | null>, name: Ref<string | null>) {
  const queryKey = computed(() => QUERY_KEYS.promptDetail(source.value ?? "", name.value ?? ""));
  return useQuery({
    queryKey,
    queryFn: async () => {
      const s = source.value!;
      const n = name.value!;
      if (s === "bundled") {
        return api.get<PromptDetail>(`/api/prompts/bundled/${n}`);
      }
      const body = await api.get<{ name: string; content: string; bundled: boolean }>(
        `/api/projects/${s}/prompts/${n}`,
      );
      return { name: body.name, source: s, content: body.content } satisfies PromptDetail;
    },
    enabled: computed(() => !!source.value && !!name.value),
  });
}

/** Save a prompt to a project. Source must be a project slug, never "bundled". */
export function useSavePrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { source: string; name: string; content: string }) => {
      if (body.source === "bundled") {
        throw new Error("bundled prompts are read-only — pick a project");
      }
      return api.post(`/api/projects/${body.source}/prompts`, {
        name: body.name,
        content: body.content,
      });
    },
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.prompts() });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.prompts(vars.source) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.projectPrompts(vars.source) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.promptDetail(vars.source, vars.name) });
    },
  });
}

export function useDeletePrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { source: string; name: string }) => {
      if (body.source === "bundled") {
        throw new Error("bundled prompts cannot be deleted");
      }
      return api.del(`/api/projects/${body.source}/prompts/${body.name}`);
    },
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.prompts() });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.prompts(vars.source) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.projectPrompts(vars.source) });
    },
  });
}

/** Composite: read a bundled prompt's content + write it to a project as a
 *  new prompt (under the same name unless renamed). */
export function useCopyBundledPromptToProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { fromName: string; toProject: string; toName?: string }) => {
      const detail = await api.get<PromptDetail>(`/api/prompts/bundled/${body.fromName}`);
      const targetName = body.toName ?? body.fromName;
      await api.post(`/api/projects/${body.toProject}/prompts`, {
        name: targetName,
        content: detail.content,
      });
      return { source: body.toProject, name: targetName };
    },
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.prompts() });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.prompts(data.source) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.projectPrompts(data.source) });
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
