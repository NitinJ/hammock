<template>
  <section class="space-y-4">
    <header>
      <h1 class="text-xl font-semibold text-text-primary">Review inbox</h1>
      <p class="text-sm text-text-secondary">
        Pending human-in-the-loop gates across all jobs. Both workflow-declared (<em>explicit</em>)
        and Claude-initiated
        <code class="rounded bg-surface-highlight px-1">ask_human</code>
        (<em>implicit</em>) appear here.
      </p>
    </header>

    <div v-if="hil.isPending.value" class="text-text-secondary">Loading…</div>
    <div v-else-if="hil.isError.value" class="text-red-400">
      Failed to load HIL: {{ hil.error.value?.message }}
    </div>
    <div v-else-if="!hil.data.value || hil.data.value.length === 0" class="text-text-secondary">
      Nothing waiting on a human right now.
    </div>

    <ul v-else class="space-y-3">
      <li
        v-for="item in hil.data.value"
        :key="hilKey(item)"
        class="rounded-md border border-border bg-surface-raised p-4"
      >
        <header class="mb-3 flex flex-wrap items-center justify-between gap-2 text-sm">
          <div class="flex items-center gap-2">
            <span class="font-mono text-xs text-text-primary">{{ item.job_slug }}</span>
            <span class="text-text-secondary">·</span>
            <span class="text-text-secondary">{{ item.workflow_name }}</span>
            <span class="text-text-secondary">·</span>
            <span class="font-mono text-xs text-text-primary">{{ item.node_id }}</span>
            <span
              v-if="item.iter.length > 0"
              class="rounded bg-surface-highlight px-1.5 py-0.5 text-xs text-text-secondary"
            >
              iter [{{ item.iter.join(", ") }}]
            </span>
          </div>
          <span
            :class="[
              'rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset',
              item.kind === 'implicit'
                ? 'bg-purple-500/20 text-purple-300 ring-purple-500/30'
                : 'bg-amber-500/20 text-amber-300 ring-amber-500/30',
            ]"
          >
            {{ item.kind }}
          </span>
        </header>

        <div v-if="item.kind === 'explicit'" class="space-y-3">
          <div
            v-if="presentationTitle(item)"
            class="rounded-md border border-border bg-surface px-3 py-2 text-sm text-text-primary"
          >
            {{ presentationTitle(item) }}
          </div>
          <div v-for="varName in item.output_var_names" :key="varName" class="space-y-2">
            <div class="text-xs text-text-secondary">
              Output <code class="text-text-primary">{{ varName }}</code> ·
              <code class="text-text-primary">{{ item.output_types[varName] }}</code>
            </div>
            <FormRenderer
              :type-name="item.output_types[varName] ?? ''"
              :fields="item.form_schemas[varName] ?? []"
              :on-submit="(value: Record<string, string>) => submitExplicit(item, varName, value)"
            />
          </div>
        </div>

        <div v-else-if="item.kind === 'implicit'">
          <AskHumanDisplay
            :question="item.question ?? ''"
            :on-submit="(answer: string) => submitImplicit(item, answer)"
          />
        </div>
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { useQueryClient } from "@tanstack/vue-query";
import { api } from "@/api/client";
import { useHilQueue } from "@/api/queries";
import type { HilQueueItem } from "@/api/schema.d";
import AskHumanDisplay from "@/components/hil/AskHumanDisplay.vue";
import FormRenderer from "@/components/hil/FormRenderer.vue";

const qc = useQueryClient();
const hil = useHilQueue();

function hilKey(item: HilQueueItem): string {
  if (item.kind === "implicit") return `i:${item.job_slug}:${item.call_id}`;
  return `e:${item.job_slug}:${item.node_id}:${item.iter.join(",")}`;
}

function presentationTitle(item: HilQueueItem): string | null {
  const t = item.presentation?.title;
  return typeof t === "string" ? t : null;
}

async function submitExplicit(
  item: HilQueueItem,
  varName: string,
  value: Record<string, string>,
): Promise<void> {
  await api.post(`/hil/${item.job_slug}/${item.node_id}/answer`, {
    var_name: varName,
    value,
  });
  qc.invalidateQueries({ queryKey: ["hil"] });
  qc.invalidateQueries({ queryKey: ["jobs", "detail", item.job_slug] });
}

async function submitImplicit(item: HilQueueItem, answer: string): Promise<void> {
  if (!item.call_id) return;
  await api.post(`/hil/${item.job_slug}/asks/${item.call_id}/answer`, { answer });
  qc.invalidateQueries({ queryKey: ["hil"] });
}
</script>
