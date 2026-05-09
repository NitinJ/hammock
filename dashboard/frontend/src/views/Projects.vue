<template>
  <section class="space-y-5">
    <header class="flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-semibold text-text-primary">Projects</h1>
        <p class="text-sm text-text-secondary mt-1">
          Registered local git checkouts. Workflows run against a project; jobs clone the project
          repo per run.
        </p>
      </div>
      <RouterLink :to="{ name: 'project-new' }" class="btn-accent text-sm"
        >+ New project</RouterLink
      >
    </header>

    <div v-if="projects.isPending.value" class="text-text-tertiary">Loading…</div>
    <div v-else-if="projects.isError.value" class="text-state-failed">Failed to load projects.</div>
    <div
      v-else-if="(projects.data.value ?? []).length === 0"
      class="surface p-8 text-center text-text-tertiary"
    >
      No projects yet. Register one to get started.
    </div>
    <div v-else class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      <RouterLink
        v-for="p in projects.data.value ?? []"
        :key="p.slug"
        :to="{ name: 'project-detail', params: { slug: p.slug } }"
        class="surface p-5 hover:border-border-strong transition-colors block"
      >
        <div class="flex items-center justify-between mb-2">
          <h3 class="font-semibold text-text-primary">{{ p.name }}</h3>
          <span
            :class="[
              'size-2 rounded-full',
              p.health.path_exists && p.health.is_git_repo
                ? 'bg-state-succeeded'
                : 'bg-state-failed',
            ]"
            :title="
              p.health.path_exists && p.health.is_git_repo
                ? 'Healthy'
                : 'Path missing or not a git repo'
            "
          />
        </div>
        <p class="text-xs text-text-tertiary font-mono mb-3 truncate">{{ p.repo_path }}</p>
        <div class="flex items-center gap-3 text-[10px] text-text-tertiary">
          <span v-if="p.default_branch">branch: {{ p.default_branch }}</span>
          <span class="truncate">slug: {{ p.slug }}</span>
        </div>
      </RouterLink>
    </div>
  </section>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";

import { useProjects } from "@/api/queries";

const projects = useProjects();
</script>
