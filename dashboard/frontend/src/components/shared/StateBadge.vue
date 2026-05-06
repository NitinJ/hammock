<template>
  <span
    :class="badgeClass"
    class="inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset"
  >
    {{ label }}
  </span>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { JobState, NodeRunState } from "@/api/schema.d";

type AnyState = JobState | NodeRunState | string;

const props = defineProps<{ state: AnyState }>();

const STATE_CONFIG: Record<string, { label: string; classes: string }> = {
  // Job states (lowercase per v1)
  submitted: {
    label: "Submitted",
    classes: "bg-violet-500/20 text-violet-300 ring-violet-500/30",
  },
  running: { label: "Running", classes: "bg-blue-500/20 text-blue-300 ring-blue-500/30" },
  blocked_on_human: {
    label: "Attention",
    classes: "bg-amber-500/20 text-amber-300 ring-amber-500/30",
  },
  completed: { label: "Completed", classes: "bg-green-500/20 text-green-300 ring-green-500/30" },
  failed: { label: "Failed", classes: "bg-red-500/20 text-red-300 ring-red-500/30" },
  cancelled: { label: "Cancelled", classes: "bg-gray-500/20 text-gray-400 ring-gray-500/30" },
  // Node run states
  pending: { label: "Pending", classes: "bg-gray-500/20 text-gray-400 ring-gray-500/30" },
  succeeded: { label: "Succeeded", classes: "bg-green-500/20 text-green-300 ring-green-500/30" },
  skipped: { label: "Skipped", classes: "bg-gray-500/20 text-gray-400 ring-gray-500/30" },
};

const config = computed(
  () =>
    STATE_CONFIG[props.state] ?? {
      label: String(props.state),
      classes: "bg-gray-500/20 text-gray-400 ring-gray-500/30",
    },
);

const label = computed(() => config.value.label);
const badgeClass = computed(() => config.value.classes);
</script>
