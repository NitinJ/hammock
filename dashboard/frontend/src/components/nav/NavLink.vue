<template>
  <li>
    <RouterLink
      :to="to"
      class="flex items-center gap-2 rounded-md px-2 py-1.5 text-text-secondary transition-colors hover:bg-surface-highlight hover:text-text-primary"
      active-class="bg-surface-highlight text-text-primary"
    >
      <span class="w-4 text-center text-base leading-none" aria-hidden="true">{{ icon }}</span>
      <slot />
    </RouterLink>
  </li>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";
import type { RouteLocationRaw } from "vue-router";

defineProps<{
  to: RouteLocationRaw;
  icon?: string;
}>();
</script>
