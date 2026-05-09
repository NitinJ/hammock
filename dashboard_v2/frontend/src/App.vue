<template>
  <div class="min-h-screen flex flex-col">
    <header class="sticky top-0 z-10 backdrop-blur-md bg-bg/70 border-b border-border">
      <div class="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between gap-6">
        <RouterLink :to="{ name: 'jobs' }" class="flex items-center gap-3 shrink-0">
          <span
            class="size-7 rounded-lg bg-gradient-to-br from-accent to-accent-soft shadow-glow"
          ></span>
          <span class="font-semibold tracking-tight text-text-primary">Hammock</span>
          <span class="text-xs font-mono text-text-tertiary">v2</span>
        </RouterLink>
        <nav class="flex items-center gap-1 text-sm">
          <RouterLink
            :to="{ name: 'jobs' }"
            :class="[
              'px-3 py-1.5 rounded-md transition-colors',
              isJobsActive
                ? 'bg-bg-elevated text-text-primary'
                : 'text-text-tertiary hover:text-text-secondary',
            ]"
          >
            Jobs
          </RouterLink>
          <RouterLink
            :to="{ name: 'workflows' }"
            :class="[
              'px-3 py-1.5 rounded-md transition-colors',
              isWorkflowsActive
                ? 'bg-bg-elevated text-text-primary'
                : 'text-text-tertiary hover:text-text-secondary',
            ]"
          >
            Workflows
          </RouterLink>
          <RouterLink
            :to="{ name: 'projects' }"
            :class="[
              'px-3 py-1.5 rounded-md transition-colors',
              isProjectsActive
                ? 'bg-bg-elevated text-text-primary'
                : 'text-text-tertiary hover:text-text-secondary',
            ]"
          >
            Projects
          </RouterLink>
          <RouterLink
            :to="{ name: 'prompts' }"
            :class="[
              'px-3 py-1.5 rounded-md transition-colors',
              isPromptsActive
                ? 'bg-bg-elevated text-text-primary'
                : 'text-text-tertiary hover:text-text-secondary',
            ]"
          >
            Prompts
          </RouterLink>
        </nav>
        <div class="ml-auto flex items-center gap-2">
          <RouterLink
            :to="{ name: 'new-job' }"
            class="btn-accent text-sm"
            v-if="$route.name !== 'new-job'"
          >
            + New job
          </RouterLink>
        </div>
      </div>
    </header>
    <main class="flex-1 max-w-7xl mx-auto w-full px-6 py-8">
      <RouterView />
    </main>
    <footer class="border-t border-border py-3 text-xs text-text-tertiary text-center font-mono">
      hammock v2 · claude-code as orchestrator
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";

const route = useRoute();
const isJobsActive = computed(() =>
  ["jobs", "job-detail", "orchestrator", "new-job"].includes(String(route.name)),
);
const isWorkflowsActive = computed(() =>
  [
    "workflows",
    "workflow-detail",
    "workflow-edit",
    "workflow-new",
    "project-workflow-new",
    "project-workflow-edit",
  ].includes(String(route.name)),
);
const isProjectsActive = computed(() =>
  ["projects", "project-detail", "project-new"].includes(String(route.name)),
);
const isPromptsActive = computed(() =>
  ["prompts", "prompt-detail", "prompt-edit", "prompt-new"].includes(String(route.name)),
);
</script>
