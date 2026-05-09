<template>
  <span :class="['pill', `pill-${state}`]">
    <span class="size-1.5 rounded-full" :class="dotClass" />
    {{ label }}
  </span>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  state:
    | "pending"
    | "running"
    | "succeeded"
    | "failed"
    | "awaiting"
    | "submitted"
    | "completed"
    | "blocked_on_human"
    | "paused"
    | "cancelled";
}>();

const label = computed(() => {
  if (props.state === "blocked_on_human") return "awaiting";
  return props.state;
});

const dotClass = computed(() => {
  switch (props.state) {
    case "running":
      return "bg-state-running animate-pulse";
    case "succeeded":
    case "completed":
      return "bg-state-succeeded";
    case "failed":
    case "cancelled":
      return "bg-state-failed";
    case "awaiting":
    case "blocked_on_human":
      return "bg-state-awaiting animate-pulse";
    case "paused":
      return "bg-amber-400";
    case "submitted":
      return "bg-accent-soft animate-pulse";
    default:
      return "bg-state-pending";
  }
});
</script>
