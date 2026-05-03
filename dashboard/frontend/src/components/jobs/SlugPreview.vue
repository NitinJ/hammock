<template>
  <div class="font-mono text-sm">
    <span v-if="slug" class="text-text-primary">{{ slug }}</span>
    <span v-else class="text-text-secondary italic">enter a title to preview slug</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  title: string;
}>();

function deriveSlug(input: string): string {
  const name = input.split(/[/\\]/).pop() ?? input;
  const lowered = name.toLowerCase();
  const sanitized = lowered.replace(/[^a-z0-9]+/g, "-");
  const collapsed = sanitized.replace(/-+/g, "-");
  const stripped = collapsed.replace(/^-+|-+$/g, "");
  return stripped.slice(0, 32).replace(/-+$/, "");
}

function todayPrefix(): string {
  const d = new Date();
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

const slug = computed(() => {
  const titleSlug = deriveSlug(props.title);
  if (!titleSlug) return "";
  return `${todayPrefix()}-${titleSlug}`;
});
</script>
