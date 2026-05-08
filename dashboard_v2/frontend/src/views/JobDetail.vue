<template>
  <section class="space-y-5">
    <header class="surface p-5">
      <div class="flex items-center justify-between mb-3">
        <RouterLink
          :to="{ name: 'jobs' }"
          class="text-xs text-text-tertiary hover:text-text-secondary"
        >
          ← Jobs
        </RouterLink>
        <RouterLink
          v-if="job.data.value"
          :to="{ name: 'orchestrator', params: { slug: slugRef } }"
          class="text-xs text-text-tertiary hover:text-text-secondary"
        >
          Orchestrator transcript →
        </RouterLink>
      </div>
      <div class="flex items-center justify-between gap-4 mb-3">
        <h1 class="font-mono text-base font-medium text-text-primary truncate">{{ slugRef }}</h1>
        <StatePill v-if="job.data.value" :state="job.data.value.state" />
      </div>
      <p v-if="job.data.value?.request" class="text-sm text-text-secondary line-clamp-3">
        {{ job.data.value.request }}
      </p>
      <div
        v-if="job.data.value?.error"
        class="mt-3 surface bg-state-failed/10 border-state-failed/40 p-3 text-xs text-state-failed"
      >
        {{ job.data.value.error }}
      </div>
    </header>

    <div v-if="job.isPending.value" class="text-text-tertiary">Loading…</div>

    <div v-else-if="job.data.value" class="grid grid-cols-12 gap-5">
      <aside class="col-span-12 lg:col-span-4 surface p-3 max-h-[70vh] overflow-auto">
        <div class="text-xs uppercase tracking-wider text-text-tertiary px-2 py-1 mb-2">Nodes</div>
        <ul class="space-y-1">
          <li v-for="(node, i) in job.data.value.nodes" :key="node.id" class="relative">
            <span
              v-if="i < job.data.value.nodes.length - 1"
              class="absolute left-[14px] top-7 bottom-0 w-px bg-border pointer-events-none"
            />
            <button
              type="button"
              :class="[
                'w-full text-left px-2 py-2 rounded-lg flex items-start gap-3 group transition-colors',
                selectedNodeId === node.id
                  ? 'bg-bg-elevated border border-border-strong'
                  : 'hover:bg-bg-raised border border-transparent',
              ]"
              @click="selectedNodeId = node.id"
            >
              <span :class="['size-2 mt-1.5 rounded-full shrink-0', dotColor(node)]" />
              <span class="flex-1 min-w-0">
                <span class="block text-sm text-text-primary truncate">{{ node.id }}</span>
                <span class="text-xs text-text-tertiary"
                  >{{ node.state }}<span v-if="node.awaiting_human"> · awaiting human</span></span
                >
              </span>
            </button>
          </li>
        </ul>
      </aside>

      <main class="col-span-12 lg:col-span-8">
        <div v-if="!selectedNodeId" class="surface p-12 text-center">
          <p class="text-text-secondary">Select a node from the timeline.</p>
        </div>
        <NodePane v-else :slug="slugRef" :node-id="selectedNodeId" />
      </main>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import StatePill from "@/components/StatePill.vue";
import NodePane from "@/components/NodePane.vue";
import { useJob } from "@/api/queries";
import type { NodeOverview } from "@/api/types";

const props = defineProps<{ slug: string }>();
const slugRef = computed(() => props.slug);
const job = useJob(slugRef);
const selectedNodeId = ref<string | null>(null);

watch(
  () => job.data.value?.nodes,
  (nodes) => {
    if (!selectedNodeId.value && nodes && nodes.length > 0) {
      // Default to first awaiting node, else first running, else first node.
      const awaiting = nodes.find((n) => n.awaiting_human);
      const running = nodes.find((n) => n.state === "running");
      selectedNodeId.value = awaiting?.id ?? running?.id ?? nodes[0]?.id ?? null;
    }
  },
  { immediate: true },
);

function dotColor(node: NodeOverview): string {
  if (node.awaiting_human) return "bg-state-awaiting animate-pulse";
  switch (node.state) {
    case "running":
      return "bg-state-running animate-pulse";
    case "succeeded":
      return "bg-state-succeeded";
    case "failed":
      return "bg-state-failed";
    default:
      return "bg-state-pending";
  }
}
</script>
