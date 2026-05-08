<template>
  <section class="max-w-2xl mx-auto">
    <header class="mb-6">
      <h1 class="text-2xl font-semibold text-text-primary">New job</h1>
      <p class="text-sm text-text-secondary mt-1">
        Pick a workflow and describe what you want done.
      </p>
    </header>

    <form @submit.prevent="onSubmit" class="surface p-6 space-y-5">
      <div>
        <label class="block text-xs uppercase tracking-wider text-text-tertiary mb-2">
          Workflow
        </label>
        <select v-model="workflowName" class="input">
          <option v-for="wf in workflows.data.value?.workflows ?? []" :key="wf.name" :value="wf.name">
            {{ wf.name }} — {{ wf.description ?? "" }}
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
import { ref } from "vue";
import { useRouter, RouterLink } from "vue-router";

import { useSubmitJob, useWorkflows } from "@/api/queries";

const router = useRouter();
const workflows = useWorkflows();
const submit = useSubmitJob();

const workflowName = ref("fix-bug");
const requestText = ref("");
const error = ref<string | null>(null);

async function onSubmit(): Promise<void> {
  error.value = null;
  try {
    const r = await submit.mutateAsync({
      workflow: workflowName.value,
      request: requestText.value.trim(),
    });
    await router.push({ name: "job-detail", params: { slug: r.slug } });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
</script>
