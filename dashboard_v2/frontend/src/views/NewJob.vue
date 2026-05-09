<template>
  <section class="max-w-2xl mx-auto">
    <header class="mb-6">
      <h1 class="text-2xl font-semibold text-text-primary">New job</h1>
      <p class="text-sm text-text-secondary mt-1">
        Pick a workflow, describe what you want done, attach any context (designs, logs,
        screenshots).
      </p>
    </header>

    <form @submit.prevent="onSubmit" class="surface p-6 space-y-5">
      <div>
        <label class="block text-xs uppercase tracking-wider text-text-tertiary mb-2">
          Project
        </label>
        <select v-model="projectSlug" class="input">
          <option value="">— No project (use submit-time env) —</option>
          <option v-for="p in projects.data.value ?? []" :key="p.slug" :value="p.slug">
            {{ p.name }} — {{ p.repo_path }}
          </option>
        </select>
        <p
          v-if="(projects.data.value ?? []).length === 0"
          class="text-[11px] text-text-tertiary mt-1"
        >
          No projects registered yet.
          <RouterLink :to="{ name: 'project-new' }" class="text-accent">Register one</RouterLink>
          to use per-project workflows.
        </p>
      </div>

      <div>
        <label class="block text-xs uppercase tracking-wider text-text-tertiary mb-2">
          Workflow
        </label>
        <select v-model="workflowName" class="input">
          <option v-for="wf in availableWorkflows" :key="wf.name" :value="wf.name">
            {{ wf.name }}{{ wf.bundled ? " (bundled)" : " (project)" }} —
            {{ wf.description ?? "" }}
          </option>
        </select>
      </div>

      <div>
        <label class="block text-xs uppercase tracking-wider text-text-tertiary mb-2">
          Request
        </label>
        <textarea
          v-model="requestText"
          rows="8"
          placeholder="Describe the bug, feature, or task. The agent will read your repo to ground its work."
          class="input font-mono text-sm"
        />
      </div>

      <div>
        <label class="block text-xs uppercase tracking-wider text-text-tertiary mb-2">
          Attachments (optional)
        </label>
        <div
          :class="[
            'border-2 border-dashed rounded-lg p-6 text-center transition-colors cursor-pointer',
            dragOver
              ? 'border-accent bg-accent/5'
              : 'border-border hover:border-border-strong bg-bg-elevated/40',
          ]"
          @dragover.prevent="dragOver = true"
          @dragleave.prevent="dragOver = false"
          @drop.prevent="onDrop"
          @click="onPickClick"
        >
          <input ref="fileInput" type="file" multiple class="hidden" @change="onPick" />
          <p class="text-sm text-text-secondary">
            Drag & drop files here, or
            <span class="text-accent underline">browse</span>
          </p>
          <p class="text-xs text-text-tertiary mt-1">
            Designs, error logs, screenshots, anything. Max 50&nbsp;MB total.
          </p>
        </div>

        <ul v-if="files.length > 0" class="mt-3 space-y-1">
          <li
            v-for="(f, i) in files"
            :key="`${f.name}-${i}`"
            class="flex items-center justify-between text-xs surface px-3 py-2"
          >
            <span class="font-mono truncate">{{ f.name }}</span>
            <span class="flex items-center gap-3">
              <span class="text-text-tertiary">{{ formatBytes(f.size) }}</span>
              <button
                type="button"
                class="text-text-tertiary hover:text-state-failed"
                @click="removeFile(i)"
              >
                ✕
              </button>
            </span>
          </li>
        </ul>
      </div>

      <div v-if="error" class="text-state-failed text-sm">{{ error }}</div>

      <div class="flex items-center justify-end gap-2">
        <RouterLink :to="{ name: 'jobs' }" class="btn-ghost text-sm">Cancel</RouterLink>
        <button
          type="submit"
          class="btn-accent text-sm"
          :disabled="submit.isPending.value || !workflowName || !requestText.trim()"
        >
          {{ submit.isPending.value ? "Submitting…" : "Submit job" }}
        </button>
      </div>
    </form>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, toRef, watch } from "vue";
import { useRoute, useRouter, RouterLink } from "vue-router";

import { useProjectWorkflows, useProjects, useSubmitJob, useWorkflows } from "@/api/queries";

const router = useRouter();
const route = useRoute();
const projects = useProjects();
const workflows = useWorkflows();
const submit = useSubmitJob();

const projectSlug = ref(typeof route.query.project === "string" ? route.query.project : "");
const projectSlugRef = toRef(projectSlug);
const projectWorkflows = useProjectWorkflows(projectSlugRef);

const availableWorkflows = computed(() => {
  if (projectSlug.value && projectWorkflows.data.value) {
    return projectWorkflows.data.value.workflows;
  }
  return workflows.data.value?.workflows ?? [];
});

const workflowName = ref(
  typeof route.query.workflow === "string" ? route.query.workflow : "fix-bug",
);
watch(availableWorkflows, (list) => {
  if (list.length === 0) return;
  if (!list.some((w) => w.name === workflowName.value)) {
    const first = list[0];
    if (first) workflowName.value = first.name;
  }
});

const requestText = ref("");
const error = ref<string | null>(null);
const files = ref<File[]>([]);
const dragOver = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

function onPickClick(): void {
  fileInput.value?.click();
}

function onPick(event: Event): void {
  const input = event.target as HTMLInputElement;
  if (input.files) {
    for (const f of Array.from(input.files)) {
      files.value.push(f);
    }
    input.value = "";
  }
}

function onDrop(event: DragEvent): void {
  dragOver.value = false;
  if (!event.dataTransfer) return;
  for (const f of Array.from(event.dataTransfer.files)) {
    files.value.push(f);
  }
}

function removeFile(idx: number): void {
  files.value.splice(idx, 1);
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function onSubmit(): Promise<void> {
  error.value = null;
  try {
    const r = await submit.mutateAsync({
      workflow: workflowName.value,
      request: requestText.value.trim(),
      project_slug: projectSlug.value || undefined,
      artifacts: files.value.length > 0 ? files.value : undefined,
    });
    await router.push({ name: "job-detail", params: { slug: r.slug } });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
</script>
