<template>
  <section>
    <header class="mb-6 flex items-end justify-between">
      <div>
        <h1 class="text-2xl font-semibold text-text-primary">Jobs</h1>
        <p class="text-sm text-text-secondary mt-1">
          Multi-stage workflows orchestrated by claude.
        </p>
      </div>
      <div class="text-xs font-mono text-text-tertiary">
        {{ jobs.data.value?.length ?? 0 }} job<span v-if="jobs.data.value?.length !== 1">s</span>
      </div>
    </header>

    <div v-if="jobs.isPending.value" class="text-text-tertiary text-sm">Loading…</div>
    <div v-else-if="jobs.isError.value" class="text-state-failed text-sm">Failed to load jobs.</div>
    <div v-else-if="(jobs.data.value ?? []).length === 0" class="surface p-12 text-center">
      <h2 class="text-lg font-medium text-text-primary mb-2">No jobs yet</h2>
      <p class="text-sm text-text-secondary mb-4">
        Submit a request and watch claude run a multi-stage workflow against your repo.
      </p>
      <RouterLink :to="{ name: 'new-job' }" class="btn-accent text-sm">
        + Submit your first job
      </RouterLink>
    </div>
    <ul v-else class="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <li v-for="job in jobs.data.value" :key="job.slug">
        <RouterLink
          :to="{ name: 'job-detail', params: { slug: job.slug } }"
          class="surface surface-hover p-5 block group"
        >
          <header class="flex items-center justify-between mb-3">
            <span class="text-xs font-mono text-text-tertiary">
              {{ job.workflow_name }}
            </span>
            <StatePill :state="job.state" />
          </header>
          <p class="text-sm text-text-primary line-clamp-2 mb-3">
            {{ job.request || "(no request)" }}
          </p>
          <div class="flex items-center justify-between gap-3">
            <div class="flex items-center gap-1">
              <span
                v-for="n in job.nodes"
                :key="n.id"
                :title="`${n.id}: ${n.state}`"
                class="size-2 rounded-full"
                :class="nodeColor(n.state, n.awaiting_human)"
              />
            </div>
            <span class="text-xs font-mono text-text-tertiary">
              {{ formatDuration(job.started_at, job.finished_at) }}
            </span>
          </div>
          <div
            class="mt-3 text-xs font-mono text-text-tertiary truncate group-hover:text-text-secondary transition-colors"
          >
            {{ job.slug }}
          </div>
        </RouterLink>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";
import StatePill from "@/components/StatePill.vue";
import { useJobs } from "@/api/queries";
import { formatDuration } from "@/lib/format";

const jobs = useJobs();

function nodeColor(state: string, awaiting: boolean): string {
  if (awaiting) return "bg-state-awaiting";
  switch (state) {
    case "running":
      return "bg-state-running animate-pulse";
    case "succeeded":
      return "bg-state-succeeded";
    case "failed":
      return "bg-state-failed";
    default:
      return "bg-state-pending";
  }
}
</script>
