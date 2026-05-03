<template>
  <div class="p-6 space-y-8">
    <!-- Active stages strip -->
    <section>
      <h2 class="text-lg font-semibold text-text-primary mb-3">Active Stages</h2>
      <div v-if="stagesLoading" class="text-text-secondary text-sm">Loading…</div>
      <div v-else-if="stagesError" class="text-red-400 text-sm">Failed to load active stages.</div>
      <div v-else-if="!activeStages?.length" class="text-text-secondary italic text-sm">
        No active stages right now.
      </div>
      <div v-else class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
        <RouterLink
          v-for="s in activeStages"
          :key="`${s.job_slug}/${s.stage_id}`"
          :to="`/jobs/${s.job_slug}/stages/${s.stage_id}`"
          class="bg-surface border border-border rounded-lg p-4 hover:border-blue-400 transition-colors block"
        >
          <div class="flex items-center justify-between mb-1">
            <span class="font-mono text-sm text-text-primary">{{ s.job_slug }}</span>
            <StateBadge :state="s.state" />
          </div>
          <p class="text-xs text-text-secondary">{{ s.stage_id }}</p>
          <p class="text-xs text-text-secondary mt-1">${{ s.cost_accrued.toFixed(4) }}</p>
        </RouterLink>
      </div>
    </section>

    <!-- HIL awaiting -->
    <section>
      <h2 class="text-lg font-semibold text-text-primary mb-3">
        Awaiting HIL
        <span
          v-if="hilItems?.length"
          class="ml-2 text-sm bg-amber-900 text-amber-300 rounded-full px-2 py-0.5"
        >
          {{ hilItems.length }}
        </span>
      </h2>
      <div v-if="!hilItems?.length" class="text-text-secondary italic text-sm">
        No awaiting HIL items.
      </div>
      <ul v-else class="space-y-2">
        <li v-for="h in hilItems" :key="h.item_id">
          <RouterLink
            :to="`/hil/${h.item_id}`"
            class="flex items-center gap-3 text-sm hover:text-blue-400"
          >
            <span class="font-mono text-text-secondary">{{ h.item_id }}</span>
            <span class="text-text-primary">{{ h.job_slug }}</span>
            <span class="text-amber-400">{{ h.kind }}</span>
            <span class="text-text-secondary">{{ Math.round(h.age_seconds) }}s</span>
          </RouterLink>
        </li>
      </ul>
    </section>

    <!-- Recent jobs -->
    <section>
      <h2 class="text-lg font-semibold text-text-primary mb-3">Recent Jobs</h2>
      <div v-if="jobsLoading" class="text-text-secondary text-sm">Loading…</div>
      <div v-else-if="jobsError" class="text-red-400 text-sm">Failed to load jobs.</div>
      <div v-else-if="!recentJobs?.length" class="text-text-secondary italic text-sm">
        No jobs yet.
      </div>
      <ul v-else class="space-y-2">
        <li
          v-for="j in recentJobs"
          :key="j.job_slug"
          class="flex items-center gap-3 text-sm"
        >
          <RouterLink :to="`/jobs/${j.job_slug}`" class="font-mono hover:text-blue-400">
            {{ j.job_slug }}
          </RouterLink>
          <StateBadge :state="j.state" />
          <span class="text-text-secondary">${{ j.total_cost_usd.toFixed(4) }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";
import { useActiveStages, useHilQueue, useJobs } from "@/api/queries";
import StateBadge from "@/components/shared/StateBadge.vue";

const { data: activeStages, isPending: stagesLoading, isError: stagesError } = useActiveStages();
const { data: hilItems } = useHilQueue();
const { data: recentJobs, isPending: jobsLoading, isError: jobsError } = useJobs();
</script>
