<template>
  <span :class="badgeClass" class="inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset">
    {{ label }}
  </span>
</template>

<script setup lang="ts">
import { computed } from "vue";
import type { JobState, StageState, TaskState, HilState } from "@/api/schema.d";

type AnyState = JobState | StageState | TaskState | HilState;

const props = defineProps<{ state: AnyState }>();

const STATE_CONFIG: Record<string, { label: string; classes: string }> = {
  // Job states
  SUBMITTED: { label: "Submitted", classes: "bg-violet-500/20 text-violet-300 ring-violet-500/30" },
  STAGES_RUNNING: { label: "Running", classes: "bg-blue-500/20 text-blue-300 ring-blue-500/30" },
  BLOCKED_ON_HUMAN: { label: "Attention", classes: "bg-amber-500/20 text-amber-300 ring-amber-500/30" },
  COMPLETED: { label: "Completed", classes: "bg-green-500/20 text-green-300 ring-green-500/30" },
  ABANDONED: { label: "Abandoned", classes: "bg-gray-500/20 text-gray-400 ring-gray-500/30" },
  FAILED: { label: "Failed", classes: "bg-red-500/20 text-red-300 ring-red-500/30" },
  // Stage states
  PENDING: { label: "Pending", classes: "bg-gray-500/20 text-gray-400 ring-gray-500/30" },
  READY: { label: "Ready", classes: "bg-violet-500/20 text-violet-300 ring-violet-500/30" },
  RUNNING: { label: "Running", classes: "bg-blue-500/20 text-blue-300 ring-blue-500/30" },
  PARTIALLY_BLOCKED: { label: "Partial", classes: "bg-amber-500/10 text-amber-400 ring-amber-500/20" },
  ATTENTION_NEEDED: { label: "Attention", classes: "bg-amber-500/20 text-amber-300 ring-amber-500/30" },
  WRAPPING_UP: { label: "Wrapping Up", classes: "bg-blue-500/10 text-blue-400 ring-blue-500/20" },
  SUCCEEDED: { label: "Succeeded", classes: "bg-green-500/20 text-green-300 ring-green-500/30" },
  CANCELLED: { label: "Cancelled", classes: "bg-gray-500/20 text-gray-400 ring-gray-500/30" },
  // Task states
  STUCK: { label: "Stuck", classes: "bg-amber-500/20 text-amber-300 ring-amber-500/30" },
  DONE: { label: "Done", classes: "bg-green-500/20 text-green-300 ring-green-500/30" },
  // HIL states
  AWAITING: { label: "Awaiting", classes: "bg-amber-500/20 text-amber-300 ring-amber-500/30" },
  ANSWERED: { label: "Answered", classes: "bg-green-500/20 text-green-300 ring-green-500/30" },
};

const config = computed(
  () =>
    STATE_CONFIG[props.state] ?? {
      label: props.state,
      classes: "bg-gray-500/20 text-gray-400 ring-gray-500/30",
    },
);

const label = computed(() => config.value.label);
const badgeClass = computed(() => config.value.classes);
</script>
