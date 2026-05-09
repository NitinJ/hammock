<template>
  <section class="space-y-5">
    <header class="surface p-5">
      <div class="flex items-center justify-between mb-3">
        <RouterLink
          :to="{ name: 'workflows' }"
          class="text-xs text-text-tertiary hover:text-text-secondary"
        >
          ← Workflows
        </RouterLink>
        <div class="flex items-center gap-2">
          <RouterLink v-if="canEditInPlace" :to="editRoute" class="btn-ghost text-xs">
            Edit
          </RouterLink>
          <RouterLink
            v-if="isBundled"
            :to="{ name: 'workflow-new', query: { copy_from: name } }"
            class="btn-ghost text-xs"
          >
            Save as new (custom)
          </RouterLink>
          <RouterLink
            :to="{
              name: 'new-job',
              query: { workflow: name, ...(projectSlug ? { project: projectSlug } : {}) },
            }"
            class="btn-accent text-xs"
          >
            Use this workflow
          </RouterLink>
        </div>
      </div>
      <div class="flex items-center justify-between gap-4 mb-2">
        <h1 class="font-semibold text-2xl text-text-primary">{{ name }}</h1>
        <WorkflowSourcePill :source="effectiveSource" />
      </div>
      <p v-if="workflow?.description" class="text-sm text-text-secondary whitespace-pre-line">
        {{ workflow.description }}
      </p>
    </header>

    <div v-if="isPending" class="text-text-tertiary">Loading…</div>
    <div v-else-if="loadError" class="text-state-failed">{{ loadError }}</div>
    <template v-else-if="workflow">
      <section class="surface p-5">
        <h2 class="text-xs uppercase tracking-wider text-text-tertiary mb-3">DAG</h2>
        <DagVisualizer :nodes="workflow.nodes" />
      </section>

      <section class="surface p-5">
        <header class="flex items-center justify-between mb-3">
          <h2 class="text-xs uppercase tracking-wider text-text-tertiary">YAML source</h2>
          <button
            type="button"
            class="text-xs text-text-tertiary hover:text-text-secondary"
            @click="showYaml = !showYaml"
          >
            {{ showYaml ? "Hide" : "Show" }}
          </button>
        </header>
        <pre
          v-if="showYaml"
          class="font-mono text-xs whitespace-pre-wrap text-text-secondary bg-bg-elevated/40 rounded p-4 overflow-x-auto"
          >{{ workflow.yaml }}</pre
        >
      </section>

      <section class="surface p-5">
        <h2 class="text-xs uppercase tracking-wider text-text-tertiary mb-3">Nodes</h2>
        <ul class="space-y-2">
          <li
            v-for="node in workflow.nodes"
            :key="node.id"
            class="flex items-start gap-3 py-2 border-b border-border last:border-b-0"
          >
            <span
              :class="[
                'size-2 mt-1.5 rounded-full shrink-0',
                node.human_review ? 'bg-state-awaiting' : 'bg-accent',
              ]"
            />
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-mono text-sm text-text-primary">{{ node.id }}</span>
                <span
                  v-if="node.human_review"
                  class="text-[10px] uppercase tracking-wider text-state-awaiting bg-state-awaiting/10 px-1.5 py-0.5 rounded"
                  >HIL</span
                >
              </div>
              <p v-if="node.description" class="text-xs text-text-secondary mt-0.5">
                {{ node.description }}
              </p>
              <div class="flex flex-wrap items-center gap-2 mt-1">
                <span class="text-[10px] text-text-tertiary"
                  >prompt: <code class="font-mono">{{ node.prompt }}</code></span
                >
                <span v-if="node.after.length > 0" class="text-[10px] text-text-tertiary"
                  >after: <code class="font-mono">{{ node.after.join(", ") }}</code></span
                >
                <span
                  v-if="node.requires && node.requires.length > 0"
                  class="text-[10px] text-text-tertiary"
                  >requires: <code class="font-mono">{{ node.requires.join(", ") }}</code></span
                >
              </div>
            </div>
          </li>
        </ul>
      </section>

      <div class="flex items-center justify-end gap-2 pt-2">
        <button
          v-if="canDelete"
          type="button"
          class="btn-ghost text-xs text-state-failed hover:bg-state-failed/10"
          :disabled="deleteBusy"
          @click="onDelete"
        >
          Delete workflow
        </button>
      </div>
      <p v-if="deleteError" class="text-state-failed text-xs">{{ deleteError }}</p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import DagVisualizer from "@/components/workflow/DagVisualizer.vue";
import WorkflowSourcePill from "@/components/workflow/WorkflowSourcePill.vue";
import type { WorkflowDetail as WorkflowDetailShape } from "@/api/types";

const props = defineProps<{ name: string }>();
const route = useRoute();
const router = useRouter();

// `source` query param distinguishes which endpoint to fetch from:
// - undefined → /api/workflows/:name (custom > bundled)
// - "bundled" / "custom" → same as above
// - <project-slug> → /api/projects/:slug/workflows/:name
const sourceQuery = computed<string | null>(() => {
  const v = route.query.source;
  return typeof v === "string" && v ? v : null;
});

const workflow = ref<WorkflowDetailShape | null>(null);
const isPending = ref(false);
const loadError = ref<string | null>(null);
const showYaml = ref(false);
const deleteBusy = ref(false);
const deleteError = ref<string | null>(null);

const projectSlug = computed(() => {
  const s = sourceQuery.value;
  if (!s || s === "bundled" || s === "custom") return null;
  return s;
});

const effectiveSource = computed(() => workflow.value?.source ?? sourceQuery.value ?? "bundled");
const isBundled = computed(() => effectiveSource.value === "bundled");
const canEditInPlace = computed(() => effectiveSource.value !== "bundled");
const canDelete = computed(() => effectiveSource.value !== "bundled");

const editRoute = computed(() => {
  if (projectSlug.value) {
    return {
      name: "project-workflow-edit",
      params: { slug: projectSlug.value, name: props.name },
    };
  }
  return { name: "workflow-edit", params: { name: props.name } };
});

async function fetchWorkflow(): Promise<void> {
  isPending.value = true;
  loadError.value = null;
  try {
    const url = projectSlug.value
      ? `/api/projects/${encodeURIComponent(projectSlug.value)}/workflows/${encodeURIComponent(props.name)}`
      : `/api/workflows/${encodeURIComponent(props.name)}`;
    const r = await fetch(url);
    if (!r.ok) {
      loadError.value = `Failed to load workflow (${r.status})`;
      return;
    }
    workflow.value = (await r.json()) as WorkflowDetailShape;
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : String(e);
  } finally {
    isPending.value = false;
  }
}

onMounted(fetchWorkflow);
watch(
  () => [props.name, sourceQuery.value] as const,
  () => fetchWorkflow(),
);

async function onDelete(): Promise<void> {
  if (!canDelete.value) return;
  if (!confirm(`Delete workflow "${props.name}"? This cannot be undone.`)) return;
  deleteBusy.value = true;
  deleteError.value = null;
  try {
    const url = projectSlug.value
      ? `/api/projects/${encodeURIComponent(projectSlug.value)}/workflows/${encodeURIComponent(props.name)}`
      : `/api/workflows/${encodeURIComponent(props.name)}`;
    const r = await fetch(url, { method: "DELETE" });
    if (!r.ok) {
      deleteError.value = `Delete failed (${r.status})`;
      return;
    }
    void router.push({ name: "workflows" });
  } catch (e) {
    deleteError.value = e instanceof Error ? e.message : String(e);
  } finally {
    deleteBusy.value = false;
  }
}
</script>
