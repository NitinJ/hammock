<template>
  <div class="p-6 space-y-8">
    <div v-if="isPending" class="text-text-secondary">Loading…</div>
    <div v-else-if="isError" class="text-red-400">Project not found.</div>
    <template v-else-if="detail">
      <!-- Header -->
      <div class="flex items-start justify-between">
        <div>
          <h1 class="text-2xl font-bold text-text-primary">{{ detail.project.name }}</h1>
          <p class="text-text-secondary font-mono text-sm mt-1">{{ detail.project.slug }}</p>
        </div>
        <div class="flex gap-4 text-sm text-text-secondary">
          <span>{{ detail.total_jobs }} jobs</span>
          <span v-if="detail.open_hil_count" class="text-amber-400">
            {{ detail.open_hil_count }} awaiting HIL
          </span>
        </div>
      </div>

      <!-- Metadata -->
      <section class="bg-surface border border-border rounded-lg p-4 space-y-2 text-sm">
        <div class="flex gap-2">
          <span class="text-text-secondary w-36">Repo path</span>
          <span class="font-mono text-text-primary">{{ detail.project.repo_path }}</span>
        </div>
        <div v-if="detail.project.remote_url" class="flex gap-2">
          <span class="text-text-secondary w-36">Remote</span>
          <a :href="detail.project.remote_url" class="text-blue-400 hover:underline" target="_blank">
            {{ detail.project.remote_url }}
          </a>
        </div>
        <div class="flex gap-2">
          <span class="text-text-secondary w-36">Default branch</span>
          <span class="font-mono text-text-primary">{{ detail.project.default_branch }}</span>
        </div>
      </section>

      <!-- Jobs -->
      <section>
        <h2 class="text-lg font-semibold text-text-primary mb-3">Jobs</h2>
        <div v-if="jobsPending" class="text-text-secondary text-sm">Loading…</div>
        <div v-else-if="!jobs?.length" class="text-text-secondary italic text-sm">No jobs yet.</div>
        <ul v-else class="space-y-2">
          <li
            v-for="j in jobs"
            :key="j.job_slug"
            class="flex items-center gap-3 text-sm border-b border-border py-2"
          >
            <RouterLink :to="`/jobs/${j.job_slug}`" class="font-mono hover:text-blue-400">
              {{ j.job_slug }}
            </RouterLink>
            <StateBadge :state="j.state" />
            <span class="text-text-secondary ml-auto">${{ j.total_cost_usd.toFixed(4) }}</span>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { useProject, useJobs } from "@/api/queries";
import StateBadge from "@/components/shared/StateBadge.vue";

const route = useRoute();
const slug = computed(() => route.params["slug"] as string);

const { data: detail, isPending, isError } = useProject(slug);
const { data: jobs, isPending: jobsPending } = useJobs(slug);
</script>
