<template>
  <section class="flex h-full min-h-[calc(100vh-8rem)] flex-col">
    <header class="mb-3 flex flex-wrap items-center justify-between gap-3">
      <div>
        <RouterLink :to="{ name: 'jobs-list' }" class="text-xs text-text-secondary hover:underline">
          ← Jobs
        </RouterLink>
        <h1 class="mt-1 font-mono text-lg font-semibold text-text-primary">
          {{ jobSlug }}
        </h1>
      </div>
      <div v-if="job.data.value" class="flex items-center gap-3 text-sm">
        <span class="text-text-secondary">{{ job.data.value.workflow_name }}</span>
        <StateBadge :state="job.data.value.state" />
      </div>
    </header>

    <div v-if="job.isPending.value" class="text-text-secondary">Loading…</div>
    <div v-else-if="job.isError.value" class="text-red-400">
      Failed to load job: {{ job.error.value?.message }}
    </div>

    <div v-else-if="job.data.value" class="grid flex-1 grid-cols-12 gap-4">
      <!-- Left pane: node list with loop unrolling -->
      <aside class="col-span-4 overflow-auto rounded-md border border-border bg-surface-raised">
        <div class="border-b border-border px-3 py-2 text-xs uppercase text-text-secondary">
          Nodes
        </div>
        <ul class="divide-y divide-border/50">
          <template v-for="row in renderedRows" :key="row.key">
            <li
              v-if="row.kind === 'header'"
              class="px-3 py-1 text-xs uppercase text-text-secondary"
              :style="{ paddingLeft: `${0.75 + row.depth * 1.25}rem` }"
            >
              iter {{ row.label }}
            </li>
            <li
              v-else
              :class="[
                'cursor-pointer px-3 py-1.5 text-sm transition-colors hover:bg-surface-highlight',
                isSelected(row.entry) && 'bg-surface-highlight',
              ]"
              :style="{ paddingLeft: `${0.75 + row.entry.iter.length * 1.25}rem` }"
              @click="selectNode(row.entry)"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="font-mono text-xs text-text-primary">{{ row.entry.node_id }}</span>
                <StateBadge :state="row.entry.state" />
              </div>
              <div v-if="row.entry.actor || row.entry.kind" class="text-xs text-text-secondary">
                {{ [row.entry.actor, row.entry.kind].filter(Boolean).join(" · ") }}
              </div>
            </li>
          </template>
          <li v-if="job.data.value.nodes.length === 0" class="px-3 py-3 text-text-secondary">
            No nodes on disk yet.
          </li>
        </ul>
      </aside>

      <!-- Right pane: node detail or HIL form -->
      <main class="col-span-8 overflow-auto rounded-md border border-border bg-surface-raised p-4">
        <JobStreamPane v-if="!selectedNodeId" :job-slug="jobSlug" />

        <!-- HIL form for explicit pending nodes -->
        <div v-else-if="explicitHilForSelected" class="space-y-4">
          <div>
            <h2 class="font-mono text-sm font-semibold text-text-primary">
              {{ explicitHilForSelected.node_id }}
            </h2>
            <p class="text-xs text-text-secondary">Awaiting human input.</p>
          </div>
          <div
            v-if="hilTitle"
            class="rounded-md border border-border bg-surface px-3 py-2 text-sm text-text-primary"
          >
            {{ hilTitle }}
          </div>
          <div
            v-for="varName in explicitHilForSelected.output_var_names"
            :key="varName"
            class="space-y-2"
          >
            <div class="text-xs text-text-secondary">
              Output <code class="text-text-primary">{{ varName }}</code> ·
              <code class="text-text-primary">{{
                explicitHilForSelected.output_types[varName]
              }}</code>
            </div>
            <FormRenderer
              :type-name="explicitHilForSelected.output_types[varName] ?? ''"
              :fields="explicitHilForSelected.form_schemas[varName] ?? []"
              :on-submit="(value: Record<string, string>) => submitExplicit(varName, value)"
            />
          </div>
        </div>

        <!-- Default node detail -->
        <div v-else-if="nodeDetail.data.value" class="space-y-4">
          <div>
            <h2 class="font-mono text-sm font-semibold text-text-primary">
              {{ selectedNodeId }}
              <span v-if="iterParam.length > 0" class="text-text-secondary">
                · iter [{{ iterParam.join(", ") }}]
              </span>
            </h2>
            <div class="mt-1 flex items-center gap-3 text-xs">
              <StateBadge :state="nodeDetail.data.value.state" />
              <span class="text-text-secondary">
                attempts: {{ nodeDetail.data.value.attempts }}
              </span>
              <span v-if="nodeDetail.data.value.started_at" class="text-text-secondary">
                started {{ formatDate(nodeDetail.data.value.started_at) }}
              </span>
              <span v-if="nodeDetail.data.value.finished_at" class="text-text-secondary">
                finished {{ formatDate(nodeDetail.data.value.finished_at) }}
              </span>
            </div>
            <p
              v-if="nodeDetail.data.value.last_error"
              class="mt-2 rounded-md border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300"
            >
              {{ nodeDetail.data.value.last_error }}
            </p>
          </div>

          <div v-if="hasOutputs">
            <div class="text-xs uppercase text-text-secondary">Outputs</div>
            <pre
              v-for="(env, name) in nodeDetail.data.value.outputs"
              :key="name"
              class="mt-2 max-h-96 overflow-auto rounded-md border border-border bg-surface px-3 py-2 text-xs text-text-primary"
              >{{ name }}: {{ JSON.stringify(env, null, 2) }}</pre
            >
          </div>
          <div v-else class="text-xs text-text-secondary">No outputs produced yet.</div>
        </div>
        <div v-else-if="nodeDetail.isPending.value" class="text-text-secondary">Loading…</div>
        <div v-else class="text-text-secondary">No on-disk state for this node yet.</div>
      </main>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { useAnswerExplicitHil, useHilQueue, useJob, useNodeDetail } from "@/api/queries";
import type { HilQueueItem, NodeListEntry } from "@/api/schema.d";
import StateBadge from "@/components/shared/StateBadge.vue";
import FormRenderer from "@/components/hil/FormRenderer.vue";
import JobStreamPane from "@/components/jobs/JobStreamPane.vue";
import { buildRenderedRows } from "@/components/jobs/renderRows.ts";

const route = useRoute();
const router = useRouter();

const jobSlug = computed(() => String(route.params.jobSlug ?? ""));
const job = useJob(jobSlug);
const hil = useHilQueue(jobSlug);
const answerExplicit = useAnswerExplicitHil(jobSlug);

const selectedNodeId = computed(() => (route.query.node as string | undefined) ?? null);
const iterParam = computed<number[]>(() => {
  const raw = route.query.iter;
  if (!raw || typeof raw !== "string") return [];
  return raw
    .split(",")
    .map((tok) => Number.parseInt(tok.trim(), 10))
    .filter((n) => !Number.isNaN(n));
});

const nodeDetail = useNodeDetail(jobSlug, selectedNodeId);

const renderedRows = computed(() => buildRenderedRows(job.data.value?.nodes ?? []));

function isSelected(entry: NodeListEntry): boolean {
  if (entry.node_id !== selectedNodeId.value) return false;
  if (entry.iter.length !== iterParam.value.length) return false;
  return entry.iter.every((v, i) => v === iterParam.value[i]);
}

function selectNode(entry: NodeListEntry): void {
  router.push({
    name: "job-overview",
    params: { jobSlug: jobSlug.value },
    query: {
      node: entry.node_id,
      ...(entry.iter.length > 0 ? { iter: entry.iter.join(",") } : {}),
    },
  });
}

const explicitHilForSelected = computed<HilQueueItem | null>(() => {
  const list = hil.data.value;
  if (!list || !selectedNodeId.value) return null;
  for (const item of list) {
    if (item.kind !== "explicit") continue;
    if (item.node_id !== selectedNodeId.value) continue;
    if (item.iter.length !== iterParam.value.length) continue;
    if (item.iter.every((v, i) => v === iterParam.value[i])) return item;
  }
  return null;
});

const hilTitle = computed(() => {
  const item = explicitHilForSelected.value;
  if (!item) return null;
  const t = item.presentation?.title;
  return typeof t === "string" ? t : null;
});

async function submitExplicit(varName: string, value: Record<string, string>): Promise<void> {
  await answerExplicit.mutateAsync({
    node_id: selectedNodeId.value!,
    body: { var_name: varName, value },
  });
}

const hasOutputs = computed(() => {
  const d = nodeDetail.data.value;
  return !!d && Object.keys(d.outputs ?? {}).length > 0;
});

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

watch(jobSlug, () => {
  if (route.query.node) {
    router.replace({ name: "job-overview", params: { jobSlug: jobSlug.value } });
  }
});
</script>
