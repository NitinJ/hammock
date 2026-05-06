<template>
  <section class="space-y-6">
    <header>
      <h1 class="text-xl font-semibold text-text-primary">Dashboard</h1>
      <p class="text-sm text-text-secondary">
        Welcome to Hammock. Submit a job, watch nodes run, answer HIL gates.
      </p>
    </header>

    <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <RouterLink
        :to="{ name: 'jobs-list' }"
        class="block rounded-md border border-border bg-surface-raised p-4 hover:bg-surface-highlight"
      >
        <div class="text-xs uppercase text-text-secondary">Jobs</div>
        <div class="mt-1 text-2xl font-semibold text-text-primary">
          {{ jobs.data.value?.length ?? 0 }}
        </div>
      </RouterLink>
      <RouterLink
        :to="{ name: 'hil-queue' }"
        class="block rounded-md border border-border bg-surface-raised p-4 hover:bg-surface-highlight"
      >
        <div class="text-xs uppercase text-text-secondary">HIL pending</div>
        <div class="mt-1 text-2xl font-semibold text-text-primary">
          {{ hil.data.value?.length ?? 0 }}
        </div>
      </RouterLink>
      <RouterLink
        :to="{ name: 'job-submit' }"
        class="block rounded-md border border-border bg-surface-raised p-4 hover:bg-surface-highlight"
      >
        <div class="text-xs uppercase text-text-secondary">Submit a job</div>
        <div class="mt-1 text-sm text-text-primary">＋ New Job</div>
      </RouterLink>
    </div>

    <section v-if="recentJobs.length > 0">
      <h2 class="mb-2 text-sm font-medium text-text-primary">Recent jobs</h2>
      <ul class="divide-y divide-border/50 rounded-md border border-border bg-surface-raised">
        <li
          v-for="job in recentJobs"
          :key="job.job_slug"
          class="flex items-center justify-between gap-3 px-3 py-2 text-sm"
        >
          <RouterLink
            :to="{ name: 'job-overview', params: { jobSlug: job.job_slug } }"
            class="font-mono text-xs text-text-primary hover:underline"
          >
            {{ job.job_slug }}
          </RouterLink>
          <span class="flex items-center gap-2">
            <StateBadge :state="job.state" />
            <span class="text-xs text-text-secondary">{{ job.workflow_name }}</span>
          </span>
        </li>
      </ul>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { RouterLink } from "vue-router";
import { useHilQueue, useJobs } from "@/api/queries";
import StateBadge from "@/components/shared/StateBadge.vue";

const jobs = useJobs();
const hil = useHilQueue();

const recentJobs = computed(() => (jobs.data.value ?? []).slice(0, 5));
</script>
