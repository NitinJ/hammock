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
              {{ row.label }}
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
                <span class="text-sm text-text-primary">{{
                  row.entry.name ?? row.entry.node_id
                }}</span>
                <StateBadge :state="row.entry.state" />
              </div>
              <div
                v-if="row.entry.name || row.entry.actor || row.entry.kind"
                class="text-xs text-text-secondary"
              >
                <span v-if="row.entry.name" class="font-mono">{{ row.entry.node_id }}</span>
                <span v-if="row.entry.name && (row.entry.actor || row.entry.kind)"> · </span>
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
            <h2 class="text-sm font-semibold text-text-primary">
              {{ selectedNodeName }}
            </h2>
            <div
              v-if="selectedNodeName !== selectedNodeId"
              class="font-mono text-xs text-text-secondary"
            >
              {{ explicitHilForSelected.node_id }}
            </div>
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
            <h2 class="text-sm font-semibold text-text-primary">
              {{ selectedNodeName }}
              <span v-if="iterParam.length > 0" class="text-text-secondary">
                · iter [{{ iterParam.join(", ") }}]
              </span>
            </h2>
            <div
              v-if="selectedNodeName !== selectedNodeId"
              class="font-mono text-xs text-text-secondary"
            >
              {{ selectedNodeId }}
            </div>
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
            <div v-for="(env, name) in nodeDetail.data.value.outputs" :key="name" class="mt-2">
              <div class="mb-1 font-mono text-xs text-text-secondary">{{ name }}</div>
              <EnvelopeView :name="String(name)" :envelope="env" />
            </div>
          </div>
          <div
            v-else-if="isSucceededWithoutOutput"
            data-testid="empty-output-panel"
            class="rounded-md border border-border bg-surface px-4 py-3"
          >
            <div class="text-sm text-text-primary">Node completed — no output produced.</div>
            <div class="mt-1 text-xs text-text-secondary">
              This node didn't write any envelopes. If logs are useful, they're under the node's
              attempt directory on disk.
            </div>
          </div>
          <div v-else class="text-xs text-text-secondary">No outputs produced yet.</div>
        </div>
        <div v-else-if="nodeDetail.isPending.value" class="text-text-secondary">Loading…</div>
        <div v-else-if="isNodeNotStartedError" class="space-y-2">
          <div class="text-text-secondary">
            <span class="text-sm text-text-primary">{{ selectedNodeName }}</span>
            <span v-if="iterParam.length > 0"> · iter [{{ iterParam.join(", ") }}]</span>
            <div v-if="selectedNodeName !== selectedNodeId" class="font-mono text-xs">
              {{ selectedNodeId }}
            </div>
          </div>
          <p class="text-text-secondary">
            Not started yet. The engine writes node state on first dispatch — once it reaches this
            node, detail and outputs will show here.
          </p>
        </div>
        <div v-else class="text-red-400">
          Failed to load node detail: {{ nodeDetail.error.value?.message ?? "unknown error" }}
        </div>
      </main>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { ApiError } from "@/api/client";
import { useAnswerExplicitHil, useHilQueue, useJob, useNodeDetail } from "@/api/queries";
import type { HilQueueItem, NodeListEntry } from "@/api/schema.d";
import StateBadge from "@/components/shared/StateBadge.vue";
import FormRenderer from "@/components/hil/FormRenderer.vue";
import EnvelopeView from "@/components/jobs/EnvelopeView.vue";
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

const renderedRows = computed(() =>
  buildRenderedRows(job.data.value?.nodes ?? [], job.data.value?.loop_names ?? {}),
);

/** Display name for the selected node — picks the name from any matching
 *  node row (by node_id), or falls back to node_id when no name is set. */
const selectedNodeName = computed(() => {
  const id = selectedNodeId.value;
  if (!id) return "";
  const match = (job.data.value?.nodes ?? []).find((n) => n.node_id === id);
  return match?.name ?? id;
});

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

/** A node that completed but produced no envelopes — e.g. tests-and-fix
 *  with `tests_pr?` optional, no commits → no PR. Show a clear
 *  "completed, nothing to display" panel rather than the generic
 *  "no outputs produced yet" placeholder which reads like in-progress. */
const isSucceededWithoutOutput = computed(() => {
  const d = nodeDetail.data.value;
  if (!d) return false;
  if (d.state !== "succeeded") return false;
  return Object.keys(d.outputs ?? {}).length === 0;
});

/** Distinguish "node hasn't been dispatched yet" (404) from real failures.
 *  Loop body rows always render in the left pane (so the operator sees
 *  workflow structure upfront) but the engine only writes
 *  ``nodes/<id>/state.json`` on first dispatch — clicking a not-yet-run
 *  row 404s. Surface a friendly placeholder for that case. */
const isNodeNotStartedError = computed(() => {
  const err = nodeDetail.error.value;
  return err instanceof ApiError && err.status === 404;
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
