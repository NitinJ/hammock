<template>
  <div class="flex gap-2 py-0.5 text-xs text-text-secondary font-mono">
    <span class="shrink-0">▸</span>
    <span class="font-semibold text-text-primary">{{ toolName }}</span>
    <span class="truncate flex-1">{{ result }}</span>
    <span class="shrink-0">{{ durationLabel }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  toolName: string;
  result: string;
  durationMs: number;
  timestamp: string;
}>();

const durationLabel = computed(() => {
  const s = props.durationMs / 1000;
  return s >= 1 ? `${s.toFixed(1)}s` : `${props.durationMs}ms`;
});
</script>
