<template>
  <section class="space-y-5">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-semibold text-text-primary">Prompts</h1>
        <p class="text-sm text-text-secondary mt-1">
          Reusable agent prompts. Bundled prompts ship with Hammock; project prompts are saved per
          project under <code class="font-mono">.hammock-v2/prompts/</code>.
        </p>
      </div>
      <RouterLink :to="{ name: 'prompt-new' }" class="btn-accent text-sm">
        + New prompt
      </RouterLink>
    </header>

    <div class="flex items-center gap-3 text-sm">
      <label class="text-text-tertiary">Source</label>
      <select
        v-model="sourceFilter"
        class="bg-bg-elevated border border-border rounded-md px-2 py-1 text-text-primary"
      >
        <option :value="null">All</option>
        <option value="bundled">Bundled</option>
        <option v-for="p in projects.data.value ?? []" :key="p.slug" :value="p.slug">
          {{ p.slug }}
        </option>
      </select>
    </div>

    <div v-if="prompts.isPending.value" class="text-text-tertiary">Loading…</div>
    <div v-else-if="prompts.isError.value" class="text-state-failed">Failed to load prompts.</div>
    <div
      v-else-if="(prompts.data.value?.prompts ?? []).length === 0"
      class="surface p-8 text-center text-text-tertiary"
    >
      No prompts yet. Click "+ New prompt" to create one in a project.
    </div>
    <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <RouterLink
        v-for="p in prompts.data.value?.prompts ?? []"
        :key="`${p.source}/${p.name}`"
        :to="{ name: 'prompt-detail', params: { source: p.source, name: p.name } }"
        class="surface p-5 hover:border-border-strong transition-colors block"
      >
        <div class="flex items-center justify-between mb-2 gap-2">
          <h3 class="font-mono font-semibold text-text-primary truncate">{{ p.name }}</h3>
          <span
            :class="[
              'text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full whitespace-nowrap',
              p.source === 'bundled'
                ? 'bg-accent/10 text-accent'
                : 'bg-state-succeeded/10 text-state-succeeded',
            ]"
          >
            {{ p.source }}
          </span>
        </div>
        <div class="text-[11px] text-text-tertiary font-mono truncate">{{ p.path }}</div>
        <div class="flex items-center justify-between mt-3 text-[11px] text-text-tertiary">
          <span>{{ formatSize(p.size) }}</span>
          <span>{{ formatModified(p.modified_at) }}</span>
        </div>
      </RouterLink>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { RouterLink } from "vue-router";

import { useProjects, usePrompts } from "@/api/queries";

const sourceFilter = ref<string | null>(null);
const prompts = usePrompts(sourceFilter);
const projects = useProjects();

function formatSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function formatModified(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
</script>
