<template>
  <div class="flex gap-2 py-0.5 text-xs text-text-secondary">
    <span class="shrink-0">⚙</span>
    <span>{{ text }}</span>
  </div>
</template>

<script setup lang="ts">
defineProps<{ text: string; timestamp: string }>();
</script>
