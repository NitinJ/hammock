<template>
  <section class="space-y-5">
    <header class="flex items-center justify-between">
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

    <div v-if="workflows.isPending.value" class="text-text-tertiary">Loading…</div>
    <div v-else-if="workflows.isError.value" class="text-state-failed">
      Failed to load workflows.
    </div>
    <div
      v-else-if="(workflows.data.value?.workflows ?? []).length === 0"
      class="surface p-8 text-center text-text-tertiary"
    >
      No workflows yet.
    </div>
    <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <RouterLink
        v-for="wf in workflows.data.value?.workflows ?? []"
        :key="wf.name"
        :to="{ name: 'workflow-detail', params: { name: wf.name } }"
        class="surface p-5 hover:border-border-strong transition-colors block"
      >
        <div class="flex items-center justify-between mb-2">
          <h3 class="font-semibold text-text-primary">{{ wf.name }}</h3>
          <span
            :class="[
              'text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full',
              wf.bundled
                ? 'bg-accent/10 text-accent'
                : 'bg-state-succeeded/10 text-state-succeeded',
            ]"
          >
            {{ wf.bundled ? "Bundled" : "Custom" }}
          </span>
        </div>
        <p
          v-if="wf.description"
          class="text-xs text-text-secondary line-clamp-2 mb-3"
        >
          {{ wf.description }}
        </p>
        <div class="flex items-center gap-1.5 mt-3">
          <span
            v-for="(node, i) in (wf.nodes ?? []).slice(0, 8)"
            :key="`${wf.name}-${node.id}-${i}`"
            :class="[
              'size-2 rounded-full',
              node.human_review ? 'bg-state-awaiting' : 'bg-accent',
            ]"
            :title="node.id"
          />
          <span
            v-if="(wf.nodes ?? []).length > 8"
            class="text-[10px] text-text-tertiary ml-1"
          >
            +{{ (wf.nodes ?? []).length - 8 }}
          </span>
        </div>
        <p class="text-[10px] text-text-tertiary mt-2">
          {{ (wf.nodes ?? []).length }} node{{ (wf.nodes ?? []).length === 1 ? "" : "s" }}
        </p>
      </RouterLink>
    </div>
  </section>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";

import { useWorkflows } from "@/api/queries";

const workflows = useWorkflows();
</script>
