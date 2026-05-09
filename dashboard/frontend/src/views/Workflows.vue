<template>
  <section class="space-y-5">
    <header class="flex items-start justify-between gap-3 flex-wrap">
      <div>
        <h1 class="text-2xl font-semibold text-text-primary">Workflows</h1>
        <p class="text-sm text-text-secondary mt-1">
          Define what work happens, in what order. Each workflow is a small DAG of agents.
        </p>
      </div>
      <RouterLink :to="{ name: 'workflow-new' }" class="btn-accent text-sm">
        + New workflow
      </RouterLink>
    </header>

    <!-- Filter bar -->
    <div class="flex items-center gap-3 flex-wrap">
      <label class="text-xs uppercase tracking-wider text-text-tertiary">Source</label>
      <select v-model="sourceFilter" class="input text-sm w-auto">
        <option value="all">All sources</option>
        <option value="bundled">Bundled</option>
        <option value="custom">Custom</option>
        <optgroup v-if="projectSlugs.length > 0" label="Project-specific">
          <option v-for="slug in projectSlugs" :key="slug" :value="slug">
            Project: {{ slug }}
          </option>
        </optgroup>
      </select>
      <span class="text-xs text-text-tertiary">
        {{ filtered.length }} of {{ allWorkflows.length }} shown
      </span>
    </div>

    <div v-if="workflows.isPending.value" class="text-text-tertiary">Loading…</div>
    <div v-else-if="workflows.isError.value" class="text-state-failed">
      Failed to load workflows.
    </div>
    <div v-else-if="filtered.length === 0" class="surface p-8 text-center text-text-tertiary">
      No workflows match this filter.
    </div>
    <div v-else class="space-y-6">
      <!-- Group cards by source-type for visual hierarchy -->
      <div v-for="group in grouped" :key="group.label">
        <h2 class="text-xs uppercase tracking-wider text-text-tertiary mb-2">
          {{ group.label }}
        </h2>
        <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <RouterLink
            v-for="wf in group.entries"
            :key="`${wf.source}-${wf.name}`"
            :to="cardRoute(wf)"
            class="surface p-5 hover:border-border-strong transition-colors block"
          >
            <div class="flex items-center justify-between gap-2 mb-2">
              <h3 class="font-semibold text-text-primary truncate">{{ wf.name }}</h3>
              <WorkflowSourcePill :source="wf.source" />
            </div>
            <p v-if="wf.description" class="text-xs text-text-secondary line-clamp-2 mb-3">
              {{ wf.description }}
            </p>
            <div class="flex items-center gap-1.5 mt-3">
              <span
                v-for="(node, i) in (wf.nodes ?? []).slice(0, 8)"
                :key="`${wf.source}-${wf.name}-${node.id}-${i}`"
                :class="[
                  'size-2 rounded-full',
                  node.human_review ? 'bg-state-awaiting' : 'bg-accent',
                ]"
                :title="node.id"
              />
              <span v-if="(wf.nodes ?? []).length > 8" class="text-[10px] text-text-tertiary ml-1">
                +{{ (wf.nodes ?? []).length - 8 }}
              </span>
            </div>
            <p class="text-[10px] text-text-tertiary mt-2">
              {{ (wf.nodes ?? []).length }} node{{ (wf.nodes ?? []).length === 1 ? "" : "s" }}
            </p>
          </RouterLink>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, type RouteLocationRaw } from "vue-router";

import WorkflowSourcePill from "@/components/workflow/WorkflowSourcePill.vue";
import { useWorkflows } from "@/api/queries";
import type { WorkflowSummary } from "@/api/types";

const workflows = useWorkflows();
const sourceFilter = ref<string>("all");

const allWorkflows = computed<WorkflowSummary[]>(() => workflows.data.value?.workflows ?? []);

const projectSlugs = computed(() => {
  const slugs = new Set<string>();
  for (const wf of allWorkflows.value) {
    if (wf.source !== "bundled" && wf.source !== "custom") slugs.add(wf.source);
  }
  return Array.from(slugs).sort();
});

const filtered = computed(() => {
  if (sourceFilter.value === "all") return allWorkflows.value;
  return allWorkflows.value.filter((wf) => wf.source === sourceFilter.value);
});

const grouped = computed(() => {
  const buckets: { label: string; entries: WorkflowSummary[] }[] = [];
  const bundled = filtered.value.filter((w) => w.source === "bundled");
  const custom = filtered.value.filter((w) => w.source === "custom");
  const projectByslug = new Map<string, WorkflowSummary[]>();
  for (const wf of filtered.value) {
    if (wf.source === "bundled" || wf.source === "custom") continue;
    const list = projectByslug.get(wf.source) ?? [];
    list.push(wf);
    projectByslug.set(wf.source, list);
  }
  if (bundled.length > 0) buckets.push({ label: "Bundled", entries: bundled });
  if (custom.length > 0) buckets.push({ label: "Custom", entries: custom });
  for (const slug of Array.from(projectByslug.keys()).sort()) {
    buckets.push({ label: `Project: ${slug}`, entries: projectByslug.get(slug) ?? [] });
  }
  return buckets;
});

function cardRoute(wf: WorkflowSummary): RouteLocationRaw {
  // Use the global workflow-detail route for every source. The detail
  // view reads the `source` query param to fetch from the right
  // endpoint (custom/bundled hit /api/workflows; project-specific hits
  // /api/projects/:slug/workflows).
  return {
    name: "workflow-detail",
    params: { name: wf.name },
    query: wf.source !== "bundled" && wf.source !== "custom" ? { source: wf.source } : {},
  };
}
</script>
