<template>
  <div class="p-6 space-y-8">
    <div v-if="isPending" class="text-text-secondary">Loading…</div>
    <div v-else-if="isError" class="text-red-400">Job not found.</div>
    <template v-else-if="detail">
      <!-- Header -->
      <div class="flex items-start justify-between">
        <div>
          <h1 class="font-mono text-2xl font-bold text-text-primary">
            {{ detail.job.job_slug }}
          </h1>
          <p class="text-text-secondary text-sm mt-1">{{ detail.job.job_type }}</p>
        </div>
        <div class="flex items-center gap-4">
          <StateBadge :state="detail.job.state" />
          <span class="text-text-secondary text-sm">${{ detail.total_cost_usd.toFixed(4) }}</span>
        </div>
      </div>

      <!-- Stage timeline -->
      <section>
        <h2 class="text-lg font-semibold text-text-primary mb-3">Stages</h2>
        <StageTimeline :stages="detail.stages" :job-slug="detail.job.job_slug" />
      </section>

      <!-- Artifacts panel -->
      <section>
        <h2 class="text-lg font-semibold text-text-primary mb-3">Artifacts</h2>
        <div class="grid grid-cols-2 sm:grid-cols-3 gap-2 text-sm">
          <RouterLink
            v-for="artifact in stdArtifacts"
            :key="artifact"
            :to="`/jobs/${detail.job.job_slug}/artifacts/${artifact}`"
            class="text-blue-400 hover:underline font-mono"
          >
            {{ artifact }}
          </RouterLink>
        </div>
      </section>

      <!-- Cost breakdown -->
      <section>
        <h2 class="text-lg font-semibold text-text-primary mb-3">Cost by stage</h2>
        <ul class="space-y-1 text-sm">
          <li v-for="s in detail.stages" :key="s.stage_id" class="flex gap-3">
            <span class="font-mono text-text-secondary w-40 truncate">{{ s.stage_id }}</span>
            <span class="text-text-primary">${{ s.cost_accrued.toFixed(4) }}</span>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, useRoute } from "vue-router";
import { useJob } from "@/api/queries";
import StateBadge from "@/components/shared/StateBadge.vue";
import StageTimeline from "@/components/stage/StageTimeline.vue";

const route = useRoute();
const jobSlug = computed(() => route.params["jobSlug"] as string);

const { data: detail, isPending, isError } = useJob(jobSlug);

const stdArtifacts = [
  "problem-spec.md",
  "design-spec.md",
  "impl-spec.md",
  "plan.yaml",
  "summary.md",
];
</script>
