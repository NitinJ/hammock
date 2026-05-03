<template>
  <div>
    <p v-if="!stages.length" class="text-text-secondary italic text-sm">No stages yet.</p>
    <ul v-else class="space-y-1">
      <li
        v-for="s in stages"
        :key="s.stage_id"
        class="flex items-center gap-3 py-2 border-b border-border text-sm"
      >
        <RouterLink
          :to="`/jobs/${jobSlug}/stages/${s.stage_id}`"
          class="font-mono text-text-primary hover:text-blue-400 w-40 truncate"
        >
          {{ s.stage_id }}
        </RouterLink>
        <StateBadge :state="s.state" />
        <span class="text-text-secondary text-xs ml-auto">
          ${{ s.cost_accrued.toFixed(4) }}
        </span>
        <span v-if="s.started_at && s.ended_at" class="text-text-secondary text-xs">
          {{ duration(s.started_at, s.ended_at) }}
        </span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";
import type { StageListEntry } from "@/api/schema.d";
import StateBadge from "@/components/shared/StateBadge.vue";

defineProps<{ stages: StageListEntry[]; jobSlug: string }>();

function duration(startedAt: string, endedAt: string): string {
  const ms = new Date(endedAt).getTime() - new Date(startedAt).getTime();
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}
</script>
