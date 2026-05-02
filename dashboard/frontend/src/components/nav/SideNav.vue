<template>
  <nav class="flex w-52 flex-col border-r border-border bg-surface-raised px-3 py-4">
    <!-- Logo -->
    <div class="mb-6 flex items-center gap-2 px-2">
      <span class="text-lg font-semibold tracking-tight text-text-primary">🛌 Hammock</span>
    </div>

    <!-- Nav links -->
    <ul class="flex flex-1 flex-col gap-0.5 text-sm">
      <NavLink :to="{ name: 'home' }" icon="⊞">Dashboard</NavLink>
      <NavLink :to="{ name: 'project-list' }" icon="⊡">Projects</NavLink>
      <NavLink :to="{ name: 'job-submit' }" icon="＋">New Job</NavLink>

      <li class="my-2 border-t border-border" />

      <NavLink :to="{ name: 'hil-queue' }" icon="❓">
        HIL Queue
        <span
          v-if="hilCount > 0"
          class="ml-auto rounded-full bg-amber-500/20 px-1.5 py-0.5 text-xs font-medium text-amber-300"
        >
          {{ hilCount }}
        </span>
      </NavLink>
      <NavLink :to="{ name: 'cost-dashboard' }" icon="$">Costs</NavLink>
      <NavLink :to="{ name: 'settings' }" icon="⚙">Settings</NavLink>
    </ul>

    <!-- Connection status -->
    <div class="flex items-center gap-2 px-2 py-1 text-xs text-text-secondary">
      <span
        :class="connected ? 'bg-green-500' : 'bg-gray-500'"
        class="inline-block h-1.5 w-1.5 rounded-full"
      />
      {{ connected ? "Live" : "Disconnected" }}
    </div>
  </nav>
</template>

<script setup lang="ts">
import { computed } from "vue";
import { useGlobalStore } from "@/stores/global";
import NavLink from "./NavLink.vue";

const globalStore = useGlobalStore();
const hilCount = computed(() => globalStore.hilAwaitingCount);
const connected = computed(() => globalStore.connected);
</script>
