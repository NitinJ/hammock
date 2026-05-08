<template>
  <section class="space-y-5">
    <header class="surface p-5">
      <div class="flex items-center justify-between mb-3">
        <RouterLink
          :to="{ name: 'workflows' }"
          class="text-xs text-text-tertiary hover:text-text-secondary"
        >
          ← Workflows
        </RouterLink>
        <div class="flex items-center gap-2">
          <RouterLink
            v-if="!isBundled"
            :to="{ name: 'workflow-edit', params: { name } }"
            class="btn-ghost text-xs"
          >
            Edit
          </RouterLink>
          <RouterLink
            v-else
            :to="{ name: 'workflow-new', query: { copy_from: name } }"
            class="btn-ghost text-xs"
          >
            Save as new
          </RouterLink>
          <RouterLink
            :to="{ name: 'new-job', query: { workflow: name } }"
            class="btn-accent text-xs"
          >
            Use this workflow
          </RouterLink>
        </div>
      </div>
      <div class="flex items-center justify-between gap-4 mb-2">
        <h1 class="font-semibold text-2xl text-text-primary">{{ name }}</h1>
        <span
          :class="[
            'text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full',
            isBundled
              ? 'bg-accent/10 text-accent'
              : 'bg-state-succeeded/10 text-state-succeeded',
          ]"
        >
          {{ isBundled ? "Bundled" : "Custom" }}
        </span>
      </div>
      <p
        v-if="workflow.data.value?.description"
        class="text-sm text-text-secondary whitespace-pre-line"
      >
        {{ workflow.data.value.description }}
      </p>
    </header>

    <div v-if="workflow.isPending.value" class="text-text-tertiary">Loading…</div>
    <div v-else-if="workflow.isError.value" class="text-state-failed">
      Failed to load workflow.
    </div>
    <template v-else-if="workflow.data.value">
      <section class="surface p-5">
        <h2 class="text-xs uppercase tracking-wider text-text-tertiary mb-3">DAG</h2>
        <DagVisualizer :nodes="workflow.data.value.nodes" />
      </section>

      <section class="surface p-5">
        <header class="flex items-center justify-between mb-3">
          <h2 class="text-xs uppercase tracking-wider text-text-tertiary">YAML source</h2>
          <button
            type="button"
            class="text-xs text-text-tertiary hover:text-text-secondary"
            @click="showYaml = !showYaml"
          >
            {{ showYaml ? "Hide" : "Show" }}
          </button>
        </header>
        <pre
          v-if="showYaml"
          class="font-mono text-xs whitespace-pre-wrap text-text-secondary bg-bg-elevated/40 rounded p-4 overflow-x-auto"
          >{{ workflow.data.value.yaml }}</pre
        >
      </section>

      <section class="surface p-5">
        <h2 class="text-xs uppercase tracking-wider text-text-tertiary mb-3">Nodes</h2>
        <ul class="space-y-2">
          <li
            v-for="node in workflow.data.value.nodes"
            :key="node.id"
            class="flex items-start gap-3 py-2 border-b border-border last:border-b-0"
          >
            <span
              :class="[
                'size-2 mt-1.5 rounded-full shrink-0',
                node.human_review ? 'bg-state-awaiting' : 'bg-accent',
              ]"
            />
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2">
                <span class="font-mono text-sm text-text-primary">{{ node.id }}</span>
                <span
                  v-if="node.human_review"
                  class="text-[10px] uppercase tracking-wider text-state-awaiting bg-state-awaiting/10 px-1.5 py-0.5 rounded"
                  >HIL</span
                >
              </div>
              <p
                v-if="node.description"
                class="text-xs text-text-secondary mt-0.5"
              >
                {{ node.description }}
              </p>
              <div class="flex flex-wrap items-center gap-2 mt-1">
                <span class="text-[10px] text-text-tertiary"
                  >prompt: <code class="font-mono">{{ node.prompt }}</code></span
                >
                <span v-if="node.after.length > 0" class="text-[10px] text-text-tertiary"
                  >after: <code class="font-mono">{{ node.after.join(", ") }}</code></span
                >
                <span
                  v-if="node.requires && node.requires.length > 0"
                  class="text-[10px] text-text-tertiary"
                  >requires: <code class="font-mono">{{ node.requires.join(", ") }}</code></span
                >
              </div>
            </div>
          </li>
        </ul>
      </section>

      <div class="flex items-center justify-end gap-2 pt-2">
        <button
          v-if="!isBundled"
          type="button"
          class="btn-ghost text-xs text-state-failed hover:bg-state-failed/10"
          :disabled="del.isPending.value"
          @click="onDelete"
        >
          Delete workflow
        </button>
      </div>
      <p v-if="deleteError" class="text-state-failed text-xs">{{ deleteError }}</p>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";

import DagVisualizer from "@/components/workflow/DagVisualizer.vue";
import { useWorkflow, useDeleteWorkflow } from "@/api/queries";

const props = defineProps<{ name: string }>();
const router = useRouter();
const nameRef = computed(() => props.name);
const workflow = useWorkflow(nameRef);
const del = useDeleteWorkflow();

const isBundled = computed(() => workflow.data.value?.bundled ?? false);
const showYaml = ref(false);
const deleteError = ref<string | null>(null);

async function onDelete(): Promise<void> {
  deleteError.value = null;
  if (!confirm(`Delete workflow "${props.name}"? This cannot be undone.`)) return;
  try {
    await del.mutateAsync(props.name);
    void router.push({ name: "workflows" });
  } catch (e) {
    deleteError.value = e instanceof Error ? e.message : String(e);
  }
}
</script>
