<template>
  <div class="space-y-2">
    <h3 class="text-sm font-semibold text-text-primary">Dry-run plan ({{ stages.length }} stages)</h3>
    <ol class="space-y-1">
      <li
        v-for="(stage, i) in stages"
        :key="stage.id ?? i"
        class="flex gap-2 text-sm"
      >
        <span class="text-text-secondary w-6 text-right">{{ i + 1 }}.</span>
        <span class="font-mono text-text-primary">{{ stage.id }}</span>
        <span v-if="stage.description" class="text-text-secondary truncate">—
          {{ stage.description }}</span>
      </li>
    </ol>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  stages: Array<{ id?: string; description?: string; [key: string]: unknown }>;
}>();
</script>
