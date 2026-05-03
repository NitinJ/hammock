<template>
  <div class="p-6 space-y-8">
    <h1 class="text-2xl font-bold text-text-primary">Settings</h1>

    <!-- System health -->
    <section class="bg-surface border border-border rounded-lg p-4 space-y-3">
      <h2 class="text-lg font-semibold text-text-primary">System Health</h2>
      <div v-if="isPending" class="text-text-secondary text-sm">Loading…</div>
      <div v-else-if="isError" class="text-red-400 text-sm">Failed to load health data.</div>
      <template v-else-if="health">
        <div class="grid grid-cols-2 gap-4 text-sm">
          <div>
            <p class="text-text-secondary">Server status</p>
            <p class="font-semibold" :class="health.ok ? 'text-green-400' : 'text-red-400'">
              {{ health.ok ? "ok" : "error" }}
            </p>
          </div>
          <div>
            <p class="text-text-secondary">Cache entries</p>
            <p class="font-semibold text-text-primary">{{ health.cache_size }}</p>
          </div>
        </div>
      </template>
    </section>

    <!-- About -->
    <section class="bg-surface border border-border rounded-lg p-4">
      <h2 class="text-lg font-semibold text-text-primary mb-3">About</h2>
      <p class="text-text-secondary text-sm">
        Hammock Dashboard — v0. Read-only views land in Stage 12; HIL forms in Stage 13;
        job submit in Stage 14; stage live view in Stage 15.
      </p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { useHealth } from "@/api/queries";

const { data: health, isPending, isError } = useHealth();
</script>
