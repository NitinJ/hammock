<template>
  <div class="flex flex-col gap-1">
    <div class="flex items-center justify-between text-xs">
      <span class="font-mono text-text-secondary">
        ${{ costUsd.toFixed(2) }}
        <span v-if="budgetCapUsd !== null" class="text-text-secondary/60">
          / ${{ budgetCapUsd.toFixed(2) }}
        </span>
      </span>
      <span v-if="budgetCapUsd !== null" :class="pctClass" class="font-mono font-medium">
        {{ pctDisplay }}%
      </span>
    </div>
    <div v-if="budgetCapUsd !== null" class="h-1.5 w-full overflow-hidden rounded-full bg-surface-highlight">
      <div
        :class="barClass"
        class="h-full rounded-full transition-all duration-300"
        :style="{ width: `${barWidth}%` }"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  costUsd: number;
  budgetCapUsd: number | null;
}>();

const pct = computed(() => {
  if (props.budgetCapUsd === null || props.budgetCapUsd === 0) return 0;
  return (props.costUsd / props.budgetCapUsd) * 100;
});

const pctDisplay = computed(() => Math.round(pct.value));

// Clamp bar at 100%
const barWidth = computed(() => Math.min(100, pct.value));

const threshold = computed<"ok" | "warn" | "over">(() => {
  if (pct.value >= 100) return "over";
  if (pct.value >= 80) return "warn";
  return "ok";
});

const barClass = computed(() => ({
  "bg-green-500": threshold.value === "ok",       // cost-ok / green
  "bg-amber-500": threshold.value === "warn",     // cost-warn / amber
  "bg-red-500": threshold.value === "over",       // cost-over / red
}));

const pctClass = computed(() => ({
  "text-green-400": threshold.value === "ok",
  "text-amber-400": threshold.value === "warn",
  "text-red-400": threshold.value === "over",
}));
</script>
