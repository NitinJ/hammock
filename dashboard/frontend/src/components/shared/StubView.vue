<template>
  <div class="flex flex-col items-center justify-center gap-4 rounded-lg border border-dashed border-border p-16 text-center">
    <div class="text-3xl">{{ icon }}</div>
    <h1 class="text-xl font-semibold text-text-primary">{{ title }}</h1>
    <p class="max-w-sm text-sm text-text-secondary">
      <span class="inline-flex items-center gap-1 rounded bg-surface-highlight px-2 py-0.5 font-mono text-xs text-amber-300">
        TODO
      </span>
      {{ description }}
    </p>
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string;
    description?: string;
    icon?: string;
  }>(),
  { icon: "🚧" },
);
</script>
