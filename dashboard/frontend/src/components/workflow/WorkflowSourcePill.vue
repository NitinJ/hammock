<template>
  <span :class="['text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full', toneClass]">
    {{ label }}
  </span>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{ source: string }>();

const tone = computed<"bundled" | "custom" | "project">(() => {
  if (props.source === "bundled") return "bundled";
  if (props.source === "custom") return "custom";
  return "project";
});

const label = computed(() => {
  if (tone.value === "bundled") return "Bundled";
  if (tone.value === "custom") return "Custom";
  return props.source; // project slug
});

const toneClass = computed(() => {
  if (tone.value === "bundled") return "bg-bg-elevated text-text-secondary";
  if (tone.value === "custom") return "bg-accent/10 text-accent";
  return "bg-state-succeeded/10 text-state-succeeded";
});
</script>
