<template>
  <div class="p-6 space-y-6">
    <h1 class="text-2xl font-bold text-text-primary">Cost Dashboard</h1>

    <!-- Scope selector -->
    <div class="flex items-center gap-3">
      <label class="text-text-secondary text-sm">Scope:</label>
      <select
        v-model="scope"
        class="bg-surface border border-border rounded px-3 py-1 text-sm text-text-primary"
      >
        <option value="job">Job</option>
        <option value="project">Project</option>
        <option value="stage">Stage</option>
      </select>
      <input
        v-model="scopeId"
        placeholder="Enter ID…"
        class="bg-surface border border-border rounded px-3 py-1 text-sm text-text-primary flex-1 max-w-xs"
      />
    </div>

    <div v-if="!scopeId" class="text-text-secondary italic text-sm">
      Enter a {{ scope }} ID above to view costs.
    </div>
    <div v-else-if="isPending" class="text-text-secondary text-sm">Loading…</div>
    <div v-else-if="isError" class="text-red-400 text-sm">Failed to load cost data.</div>
    <template v-else-if="rollup">
      <!-- Summary -->
      <section class="bg-surface border border-border rounded-lg p-4">
        <div class="grid grid-cols-2 sm:grid-cols-3 gap-4 text-sm">
          <div>
            <p class="text-text-secondary">Scope</p>
            <p class="font-semibold text-text-primary">{{ rollup.scope }}</p>
          </div>
          <div>
            <p class="text-text-secondary">Total cost</p>
            <p class="font-semibold text-text-primary">${{ rollup.total_usd.toFixed(4) }}</p>
          </div>
          <div>
            <p class="text-text-secondary">Total tokens</p>
            <p class="font-semibold text-text-primary">{{ rollup.total_tokens.toLocaleString() }}</p>
          </div>
        </div>
      </section>

      <!-- By stage -->
      <section v-if="Object.keys(rollup.by_stage).length">
        <h2 class="text-lg font-semibold text-text-primary mb-3">By stage</h2>
        <ul class="space-y-1 text-sm">
          <li
            v-for="(cost, stageId) in rollup.by_stage"
            :key="stageId"
            class="flex gap-3 py-1 border-b border-border"
          >
            <span class="font-mono text-text-secondary w-48 truncate">{{ stageId }}</span>
            <span class="text-text-primary">${{ cost.toFixed(4) }}</span>
          </li>
        </ul>
      </section>

      <!-- By agent -->
      <section v-if="Object.keys(rollup.by_agent).length">
        <h2 class="text-lg font-semibold text-text-primary mb-3">By agent</h2>
        <ul class="space-y-1 text-sm">
          <li
            v-for="(cost, agentRef) in rollup.by_agent"
            :key="agentRef"
            class="flex gap-3 py-1 border-b border-border"
          >
            <span class="font-mono text-text-secondary w-48 truncate">{{ agentRef }}</span>
            <span class="text-text-primary">${{ cost.toFixed(4) }}</span>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from "vue";
import { useRoute } from "vue-router";
import { useCosts } from "@/api/queries";

const route = useRoute();

const scope = ref<string>((route.query["scope"] as string) ?? "job");
const scopeId = ref<string>((route.query["id"] as string) ?? "");

const { data: rollup, isPending, isError } = useCosts(
  computed(() => scope.value),
  computed(() => scopeId.value),
);
</script>
