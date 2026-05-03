<template>
  <div class="border border-border rounded my-1 text-xs">
    <button
      data-toggle
      class="w-full flex items-center gap-2 px-2 py-1 text-left hover:bg-surface-hover"
      @click="expanded = !expanded"
    >
      <span>{{ expanded ? "▾" : "▸" }}</span>
      <span class="font-mono font-semibold">{{ subagentId }}</span>
      <span class="ml-auto flex gap-2 text-text-secondary">
        <span>{{ messageCount }} msgs</span>
        <span>{{ toolCallCount }} tools</span>
        <span>${{ costUsd.toFixed(2) }}</span>
        <StateBadge :state="state" />
      </span>
    </button>
    <div v-if="expanded" data-expanded class="px-2 pb-2 pt-1 text-text-secondary">
      Subagent stream loaded on expand (full view in dedicated pane).
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue";
import StateBadge from "@/components/shared/StateBadge.vue";
import type { StageState, TaskState } from "@/api/schema.d";

defineProps<{
  subagentId: string;
  messageCount: number;
  toolCallCount: number;
  costUsd: number;
  state: StageState | TaskState;
}>();

const expanded = ref(false);
</script>
