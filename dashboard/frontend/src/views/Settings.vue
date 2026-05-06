<template>
  <section class="space-y-4">
    <header>
      <h1 class="text-xl font-semibold text-text-primary">Settings</h1>
    </header>

    <div v-if="settings.isPending.value" class="text-text-secondary">Loading…</div>
    <div v-else-if="settings.isError.value" class="text-red-400">
      Failed to load settings: {{ settings.error.value?.message }}
    </div>

    <dl
      v-else-if="settings.data.value"
      class="grid grid-cols-1 gap-3 rounded-md border border-border bg-surface-raised p-4 text-sm sm:grid-cols-3"
    >
      <div>
        <dt class="text-xs uppercase text-text-secondary">Runner mode</dt>
        <dd
          class="font-semibold"
          :class="settings.data.value.runner_mode === 'real' ? 'text-yellow-400' : 'text-green-400'"
        >
          {{ settings.data.value.runner_mode }}
        </dd>
      </div>
      <div>
        <dt class="text-xs uppercase text-text-secondary">Claude binary</dt>
        <dd class="font-mono text-xs text-text-primary">
          {{ settings.data.value.claude_binary ?? "—" }}
        </dd>
      </div>
      <div>
        <dt class="text-xs uppercase text-text-secondary">Hammock root</dt>
        <dd class="font-mono text-xs text-text-primary">
          {{ settings.data.value.root }}
        </dd>
      </div>
    </dl>

    <p class="text-xs text-text-secondary">
      Hammock Dashboard — v1. See
      <a class="underline" href="https://github.com/NitinJ/hammock">NitinJ/hammock</a>.
    </p>
  </section>
</template>

<script setup lang="ts">
import { useSettings } from "@/api/queries";

const settings = useSettings();
</script>
