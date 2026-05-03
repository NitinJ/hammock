<template>
  <div class="flex gap-2 py-1 text-sm">
    <span class="shrink-0 text-xs text-text-secondary">Agent</span>
    <p class="text-text-primary whitespace-pre-wrap">{{ text }}</p>
  </div>
</template>

<script setup lang="ts">
defineProps<{ text: string; timestamp: string }>();
</script>
