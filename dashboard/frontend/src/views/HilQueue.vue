<template>
  <div class="p-6">
    <h1 class="text-2xl font-bold text-text-primary mb-6">HIL Queue</h1>

    <div v-if="isPending" class="text-text-secondary">Loading…</div>
    <div v-else-if="isError" class="text-red-400">Failed to load HIL queue.</div>
    <div v-else-if="!items?.length" class="text-text-secondary italic">
      No awaiting items right now.
    </div>
    <table v-else class="w-full text-sm">
      <thead>
        <tr class="text-left text-text-secondary border-b border-border">
          <th class="pb-2 pr-4">ID</th>
          <th class="pb-2 pr-4">Kind</th>
          <th class="pb-2 pr-4">Job</th>
          <th class="pb-2 pr-4">Stage</th>
          <th class="pb-2 pr-4">Age (s)</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="h in items"
          :key="h.item_id"
          class="border-b border-border hover:bg-surface transition-colors"
        >
          <td class="py-2 pr-4">
            <RouterLink :to="`/hil/${h.item_id}`" class="font-mono text-blue-400 hover:underline">
              {{ h.item_id }}
            </RouterLink>
          </td>
          <td class="py-2 pr-4">
            <span
              :class="[
                'px-2 py-0.5 rounded text-xs font-medium',
                h.kind === 'ask' ? 'bg-blue-900 text-blue-300' :
                h.kind === 'review' ? 'bg-violet-900 text-violet-300' :
                'bg-amber-900 text-amber-300',
              ]"
            >
              {{ h.kind }}
            </span>
          </td>
          <td class="py-2 pr-4">
            <RouterLink :to="`/jobs/${h.job_slug}`" class="hover:text-blue-400">
              {{ h.job_slug }}
            </RouterLink>
          </td>
          <td class="py-2 pr-4 font-mono text-text-secondary">{{ h.stage_id }}</td>
          <td class="py-2 pr-4 text-text-secondary">{{ Math.round(h.age_seconds) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";
import { useHilQueue } from "@/api/queries";

const { data: items, isPending, isError } = useHilQueue();
</script>
