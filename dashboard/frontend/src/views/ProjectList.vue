<template>
  <div class="p-6">
    <h1 class="text-2xl font-bold text-text-primary mb-6">Projects</h1>

    <div v-if="isPending" class="text-text-secondary">Loading…</div>
    <div v-else-if="isError" class="text-red-400">Failed to load projects.</div>
    <div v-else-if="!projects?.length" class="text-text-secondary italic">
      No projects registered yet.
    </div>
    <ul v-else class="space-y-3">
      <li
        v-for="p in projects"
        :key="p.slug"
        class="bg-surface border border-border rounded-lg p-4 flex items-center justify-between hover:border-blue-400 transition-colors"
      >
        <div>
          <RouterLink
            :to="`/projects/${p.slug}`"
            class="font-semibold text-text-primary hover:text-blue-400"
          >
            {{ p.name }}
          </RouterLink>
          <p class="text-sm text-text-secondary mt-1">{{ p.repo_path }}</p>
        </div>
        <div class="flex items-center gap-4 text-sm">
          <span
            :class="[
              'px-2 py-0.5 rounded-full font-medium',
              p.doctor_status === 'green'
                ? 'bg-green-900 text-green-300'
                : p.doctor_status === 'yellow'
                  ? 'bg-amber-900 text-amber-300'
                  : p.doctor_status === 'red'
                    ? 'bg-red-900 text-red-300'
                    : 'bg-gray-700 text-gray-300',
            ]"
          >
            {{ p.doctor_status }}
          </span>
          <span class="text-text-secondary">
            {{ p.total_jobs }} job{{ p.total_jobs !== 1 ? "s" : "" }}
          </span>
          <span v-if="p.open_hil_count" class="text-amber-400 font-semibold">
            {{ p.open_hil_count }} HIL
          </span>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";
import { useProjects } from "@/api/queries";

const { data: projects, isPending, isError } = useProjects();
</script>
