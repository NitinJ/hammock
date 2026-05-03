<template>
  <div class="text-xs space-y-1">
    <div class="flex justify-between text-text-secondary">
      <span>${{ costUsd.toFixed(2) }} / ${{ budgetUsd.toFixed(2) }}</span>
      <span>{{ pctLabel }}</span>
    </div>
    <div class="h-2 rounded bg-surface-hover overflow-hidden" role="progressbar" :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100">
      <div
        class="h-full rounded transition-all"
        :class="pct >= 90 ? 'bg-red-500' : pct >= 70 ? 'bg-yellow-500' : 'bg-primary'"
        :style="{ width: pct + '%' }"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ costUsd: number; budgetUsd: number }>();

const pct = computed(() =>
  props.budgetUsd > 0 ? Math.min(100, Math.round((props.costUsd / props.budgetUsd) * 100)) : 0,
);

const pctLabel = computed(() => `${pct.value}%`);
</script>
