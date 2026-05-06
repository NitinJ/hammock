<template>
  <section class="space-y-4">
    <header class="flex items-center justify-between">
      <h1 class="text-xl font-semibold text-text-primary">Jobs</h1>
      <RouterLink
        :to="{ name: 'job-submit' }"
        class="rounded-md border border-border bg-surface-raised px-3 py-1.5 text-sm hover:bg-surface-highlight"
      >
        ＋ New Job
      </RouterLink>
    </header>

    <div class="flex gap-2 text-sm">
      <button
        v-for="opt in stateFilters"
        :key="opt.value ?? 'all'"
        type="button"
        :class="[
          'rounded-md border border-border px-2 py-1',
          (filter ?? null) === opt.value
            ? 'bg-surface-highlight text-text-primary'
            : 'text-text-secondary hover:bg-surface-raised',
        ]"
        @click="filter = opt.value"
      >
        {{ opt.label }}
      </button>
    </div>

    <div v-if="jobs.isPending.value" class="text-text-secondary">Loading…</div>
    <div v-else-if="jobs.isError.value" class="text-red-400">
      Failed to load jobs: {{ jobs.error.value?.message }}
    </div>
    <div v-else-if="!jobs.data.value || jobs.data.value.length === 0" class="text-text-secondary">
      No jobs yet.
    </div>

    <table v-else class="w-full text-sm">
      <thead class="text-left text-xs uppercase text-text-secondary">
        <tr class="border-b border-border">
          <th class="py-2 pr-3">Slug</th>
          <th class="py-2 pr-3">Workflow</th>
          <th class="py-2 pr-3">State</th>
          <th class="py-2 pr-3">Repo</th>
          <th class="py-2 pr-3">Submitted</th>
          <th class="py-2 pr-3">Updated</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="job in jobs.data.value"
          :key="job.job_slug"
          class="cursor-pointer border-b border-border/50 hover:bg-surface-highlight"
          @click="open(job.job_slug)"
        >
          <td class="py-2 pr-3 font-mono text-xs text-text-primary">
            {{ job.job_slug }}
          </td>
          <td class="py-2 pr-3 text-text-secondary">
            {{ job.workflow_name }}
          </td>
          <td class="py-2 pr-3">
            <StateBadge :state="job.state" />
          </td>
          <td class="py-2 pr-3 text-text-secondary">
            {{ job.repo_slug ?? "—" }}
          </td>
          <td class="py-2 pr-3 text-text-secondary">
            {{ formatDate(job.submitted_at) }}
          </td>
          <td class="py-2 pr-3 text-text-secondary">
            {{ formatDate(job.updated_at) }}
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { RouterLink, useRouter } from "vue-router";
import { useJobs } from "@/api/queries";
import type { JobState } from "@/api/schema.d";
import StateBadge from "@/components/shared/StateBadge.vue";

const router = useRouter();
const filter = ref<JobState | null>(null);
const jobs = useJobs(undefined, filter);

const stateFilters: { label: string; value: JobState | null }[] = [
  { label: "All", value: null },
  { label: "Submitted", value: "submitted" },
  { label: "Running", value: "running" },
  { label: "Blocked", value: "blocked_on_human" },
  { label: "Completed", value: "completed" },
  { label: "Failed", value: "failed" },
];

function open(slug: string): void {
  router.push({ name: "job-overview", params: { jobSlug: slug } });
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}
</script>
