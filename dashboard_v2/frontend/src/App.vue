<template>
  <div class="min-h-screen flex flex-col">
    <header
      class="sticky top-0 z-10 backdrop-blur-md bg-bg/70 border-b border-border"
    >
      <div class="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
        <RouterLink :to="{ name: 'jobs' }" class="flex items-center gap-3">
          <span class="size-7 rounded-lg bg-gradient-to-br from-accent to-accent-soft shadow-glow"></span>
          <span class="font-semibold tracking-tight text-text-primary">Hammock</span>
          <span class="text-xs font-mono text-text-tertiary">v2</span>
        </RouterLink>
        <RouterLink
          :to="{ name: 'new-job' }"
          class="btn-accent text-sm"
          v-if="$route.name !== 'new-job'"
        >
          + New job
        </RouterLink>
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
import { RouterLink, RouterView } from "vue-router";
</script>
