<template>
  <header
    class="flex h-12 items-center justify-between border-b border-border bg-surface-raised px-6"
  >
    <!-- Breadcrumb / page title -->
    <div class="text-sm font-medium text-text-primary">
      {{ currentTitle }}
    </div>

    <!-- Right slot: global actions -->
    <div class="flex items-center gap-3 text-xs text-text-secondary">
      <RouterLink
        :to="{ name: 'job-submit' }"
        class="rounded-md bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-500"
      >
        + New Job
      </RouterLink>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useRoute, RouterLink } from "vue-router";

const route = useRoute();
const currentTitle = computed(() => (route.meta?.title as string | undefined) ?? "Hammock");
</script>
