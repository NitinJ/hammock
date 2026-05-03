<template>
  <div class="flex flex-col h-screen overflow-hidden">
    <!-- Header -->
    <div class="shrink-0 px-4 py-2 border-b border-border bg-surface flex items-center gap-3">
      <RouterLink :to="{ name: 'job-overview', params: { jobSlug } }" class="text-text-secondary text-sm hover:underline">
        {{ jobSlug }}
      </RouterLink>
      <span class="text-text-secondary">/</span>
      <span class="font-semibold">{{ stageId }}</span>
      <StateBadge v-if="detail" :state="detail.stage.state" />
      <span v-if="detail" class="text-text-secondary text-xs ml-auto">
        ${{ detail.stage.cost_accrued.toFixed(2) }}
      </span>
      <button class="ml-2 text-xs text-red-500 hover:underline" @click="cancelStage">
        Cancel
      </button>
      <button class="ml-1 text-xs text-primary hover:underline" @click="restartStage">
        Restart
      </button>
    </div>

    <!-- Three-pane body -->
    <div class="flex flex-1 overflow-hidden">
      <!-- Left pane: tasks -->
      <div data-pane="left" class="w-64 shrink-0 border-r border-border overflow-y-auto p-3 space-y-4">
        <section>
          <h3 class="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-2">Tasks</h3>
          <TasksPanel :tasks="detail?.tasks ?? []" />
        </section>
      </div>

      <!-- Centre pane: Agent0 stream -->
      <div data-pane="centre" class="flex-1 flex flex-col overflow-hidden">
        <Agent0StreamPane v-if="jobSlug && stageId" :job-slug="jobSlug" :stage-id="stageId" />
      </div>

      <!-- Right pane: budget + metadata -->
      <div data-pane="right" class="w-56 shrink-0 border-l border-border overflow-y-auto p-3 space-y-4">
        <section v-if="detail">
          <h3 class="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-2">Budget</h3>
          <BudgetBar :cost-usd="detail.stage.cost_accrued" :budget-usd="10" />
        </section>
        <section>
          <h3 class="text-xs font-semibold text-text-secondary uppercase tracking-wide mb-2">Stage</h3>
          <dl class="text-xs space-y-1">
            <div class="flex justify-between">
              <dt class="text-text-secondary">Attempt</dt>
              <dd>{{ detail?.stage.attempt ?? "—" }}</dd>
            </div>
            <div class="flex justify-between">
              <dt class="text-text-secondary">Restarts</dt>
              <dd>{{ detail?.stage.restart_count ?? 0 }}</dd>
            </div>
          </dl>
        </section>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from "vue";
import { useRoute } from "vue-router";
import { api } from "@/api/client";
import type { StageDetail } from "@/api/schema.d";
import StateBadge from "@/components/shared/StateBadge.vue";
import Agent0StreamPane from "@/components/stage/Agent0StreamPane.vue";
import TasksPanel from "@/components/stage/TasksPanel.vue";
import BudgetBar from "@/components/stage/BudgetBar.vue";

const route = useRoute();
const jobSlug = computed(() => route.params["jobSlug"] as string);
const stageId = computed(() => route.params["stageId"] as string);

const detail = ref<StageDetail | null>(null);

onMounted(async () => {
  try {
    detail.value = await api.get<StageDetail>(
      `/jobs/${jobSlug.value}/stages/${stageId.value}`,
    );
  } catch {
    // stage detail unavailable — pane renders with SSE stream only
  }
});

async function cancelStage(): Promise<void> {
  try {
    await api.post(`/jobs/${jobSlug.value}/stages/${stageId.value}/cancel`);
  } catch {
    // surface error in v1+
  }
}

async function restartStage(): Promise<void> {
  try {
    await api.post(`/jobs/${jobSlug.value}/stages/${stageId.value}/restart`);
  } catch {
    // surface error in v1+
  }
}
</script>
