<template>
  <div class="flex gap-2 py-1 text-sm text-text-primary">
    <span class="text-text-secondary shrink-0 font-mono text-xs">{{ timeStr }}</span>
    <p class="whitespace-pre-wrap leading-relaxed">{{ text }}</p>
  </div>
</template>

<script setup lang="ts">
const props = defineProps<{
  text: string;
  timestamp: string;
}>();

const timeStr = new Date(props.timestamp).toLocaleTimeString();
</script>
