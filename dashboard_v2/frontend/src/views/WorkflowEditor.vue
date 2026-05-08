<template>
  <section class="space-y-5">
    <header class="surface p-5">
      <div class="flex items-center justify-between mb-3">
        <RouterLink
          :to="
            isCreate ? { name: 'workflows' } : { name: 'workflow-detail', params: { name: name } }
          "
          class="text-xs text-text-tertiary hover:text-text-secondary"
        >
          ← {{ isCreate ? "Workflows" : name }}
        </RouterLink>
        <button
          type="button"
          class="btn-accent text-sm"
          :disabled="!canSave || saveBusy"
          @click="onSave"
        >
          {{ saveBusy ? "Saving…" : "Save" }}
        </button>
      </div>
      <div v-if="isCreate" class="space-y-1.5">
        <label for="wf-name" class="text-xs uppercase tracking-wider text-text-tertiary"
          >Name</label
        >
        <input
          id="wf-name"
          v-model="newName"
          type="text"
          placeholder="my-workflow"
          class="input font-mono text-sm w-full max-w-md"
        />
      </div>
      <div v-else>
        <h1 class="font-semibold text-2xl text-text-primary">Edit · {{ name }}</h1>
      </div>
    </header>

    <div v-if="loadError" class="surface bg-state-failed/10 border-state-failed/40 p-4">
      <p class="text-sm text-state-failed">{{ loadError }}</p>
    </div>

    <div class="grid grid-cols-12 gap-5">
      <div class="col-span-12 lg:col-span-7 surface p-3 flex flex-col">
        <div class="text-xs uppercase tracking-wider text-text-tertiary px-2 py-1 mb-2">YAML</div>
        <textarea
          v-model="yamlText"
          spellcheck="false"
          class="input font-mono text-xs flex-1 min-h-[55vh] resize-y"
        />
        <p
          v-if="liveError"
          class="text-state-failed text-xs px-2 mt-2 font-mono whitespace-pre-wrap"
        >
          {{ liveError }}
        </p>
        <p v-else-if="livePending" class="text-text-tertiary text-xs px-2 mt-2">Validating…</p>
        <p v-else-if="liveNodes" class="text-text-tertiary text-xs px-2 mt-2">
          {{ liveNodes.length }} node{{ liveNodes.length === 1 ? "" : "s" }} · ready to save
        </p>
        <p v-if="saveError" class="text-state-failed text-xs px-2 mt-2 font-mono">
          {{ saveError }}
        </p>
      </div>
      <div class="col-span-12 lg:col-span-5 surface p-3 flex flex-col">
        <div class="text-xs uppercase tracking-wider text-text-tertiary px-2 py-1 mb-2">
          DAG preview
        </div>
        <div class="flex-1 overflow-auto">
          <DagVisualizer v-if="liveNodes && liveNodes.length > 0" :nodes="liveNodes" />
          <p v-else class="text-text-tertiary text-xs p-4">
            Fix the YAML to render the DAG.
          </p>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRouter, useRoute } from "vue-router";

import DagVisualizer from "@/components/workflow/DagVisualizer.vue";
import {
  useWorkflow,
  useCreateWorkflow,
  useUpdateWorkflow,
  validateWorkflowYaml,
} from "@/api/queries";
import type { WorkflowNode } from "@/api/types";

const props = defineProps<{ name?: string }>();
const router = useRouter();
const route = useRoute();

const isCreate = computed(() => !props.name);
const name = computed(() => props.name ?? "");

const newName = ref<string>("");
const yamlText = ref<string>("");
const loadError = ref<string | null>(null);
const saveError = ref<string | null>(null);
const saveBusy = ref(false);

const liveNodes = ref<WorkflowNode[] | null>(null);
const liveError = ref<string | null>(null);
const livePending = ref(false);

const initialLoad = useWorkflow(name);
const create = useCreateWorkflow();
const update = useUpdateWorkflow();

const STARTER_YAML = `name: my-workflow
description: |
  Describe what this workflow does.

nodes:
  - id: write-bug-report
    prompt: write-bug-report
    requires:
      - output.md

  - id: write-design-spec
    prompt: write-design-spec
    after: [write-bug-report]
    requires:
      - output.md
`;

onMounted(async () => {
  if (isCreate.value) {
    const copyFrom = route.query.copy_from;
    if (typeof copyFrom === "string" && copyFrom.length > 0) {
      try {
        const r = await fetch(`/api/workflows/${encodeURIComponent(copyFrom)}`);
        if (r.ok) {
          const detail = (await r.json()) as { yaml: string };
          yamlText.value = detail.yaml;
        } else {
          yamlText.value = STARTER_YAML;
        }
      } catch {
        yamlText.value = STARTER_YAML;
      }
    } else {
      yamlText.value = STARTER_YAML;
    }
  }
});

watch(
  () => initialLoad.data.value,
  (val) => {
    if (!isCreate.value && val && !yamlText.value) {
      yamlText.value = val.yaml;
    }
  },
);

const canSave = computed(() => {
  if (!yamlText.value.trim()) return false;
  if (isCreate.value && !newName.value.trim()) return false;
  if (liveError.value) return false;
  return true;
});

let validationTimer: ReturnType<typeof setTimeout> | null = null;

watch(
  yamlText,
  (val) => {
    if (validationTimer) clearTimeout(validationTimer);
    if (!val.trim()) {
      liveNodes.value = null;
      liveError.value = null;
      livePending.value = false;
      return;
    }
    livePending.value = true;
    validationTimer = setTimeout(async () => {
      try {
        const r = await validateWorkflowYaml(val);
        if (r.valid && r.nodes) {
          liveNodes.value = r.nodes;
          liveError.value = null;
        } else {
          liveError.value = r.error ?? "invalid";
        }
      } catch (e) {
        liveError.value = e instanceof Error ? e.message : String(e);
      } finally {
        livePending.value = false;
      }
    }, 500);
  },
  { immediate: true },
);

async function onSave(): Promise<void> {
  saveError.value = null;
  saveBusy.value = true;
  try {
    if (isCreate.value) {
      const created = await create.mutateAsync({
        name: newName.value.trim(),
        yaml: yamlText.value,
      });
      void router.push({ name: "workflow-detail", params: { name: created.name } });
    } else {
      const updated = await update.mutateAsync({
        name: name.value,
        yaml: yamlText.value,
      });
      void router.push({ name: "workflow-detail", params: { name: updated.name } });
    }
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : String(e);
  } finally {
    saveBusy.value = false;
  }
}
</script>
