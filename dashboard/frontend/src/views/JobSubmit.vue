<template>
  <section class="mx-auto max-w-2xl space-y-6">
    <h1 class="text-xl font-semibold text-text-primary">New Job</h1>

    <form class="space-y-4" @submit.prevent="handleSubmit">
      <div class="space-y-1">
        <label class="block text-xs uppercase text-text-secondary" for="project-select">
          Project
        </label>
        <select
          id="project-select"
          v-model="form.project_slug"
          class="w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-sm text-text-primary focus:border-blue-500 focus:outline-none"
        >
          <option value="" disabled>Select…</option>
          <option v-for="p in projects.data.value ?? []" :key="p.slug" :value="p.slug">
            {{ p.name }} ({{ p.slug }})
          </option>
        </select>
        <p v-if="projects.isError.value" class="text-xs text-red-400">
          Could not load projects: {{ projects.error.value?.message }}
        </p>
      </div>

      <div class="space-y-1">
        <label class="block text-xs uppercase text-text-secondary" for="job-type"> Job type </label>
        <input
          id="job-type"
          v-model="form.job_type"
          type="text"
          class="w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-sm text-text-primary focus:border-blue-500 focus:outline-none"
          placeholder="e.g. fix-bug"
        />
      </div>

      <div class="space-y-1">
        <label class="block text-xs uppercase text-text-secondary" for="title">Title</label>
        <input
          id="title"
          v-model="form.title"
          type="text"
          class="w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-sm text-text-primary focus:border-blue-500 focus:outline-none"
          placeholder="Short job title"
        />
      </div>

      <div class="space-y-1">
        <label class="block text-xs uppercase text-text-secondary" for="request">Request</label>
        <textarea
          id="request"
          v-model="form.request_text"
          rows="6"
          class="w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-sm text-text-primary focus:border-blue-500 focus:outline-none"
          placeholder="Describe what you want Hammock to do…"
        />
      </div>

      <div class="flex items-center gap-2">
        <input id="dry-run" v-model="form.dry_run" type="checkbox" class="accent-blue-500" />
        <label for="dry-run" class="text-sm text-text-primary">
          Dry run (validate workflow without spawning the driver)
        </label>
      </div>

      <button
        type="submit"
        :disabled="!canSubmit || submitting"
        class="rounded-md border border-blue-500 bg-blue-500/20 px-3 py-1.5 text-sm text-blue-200 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {{ submitting ? "Submitting…" : form.dry_run ? "Validate" : "Submit" }}
      </button>
    </form>

    <ul
      v-if="errors.length > 0"
      class="space-y-1 rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm"
    >
      <li v-for="(err, i) in errors" :key="i" class="text-red-300">
        <span class="font-semibold">{{ err.kind }}</span>
        <span v-if="err.stage_id"> ({{ err.stage_id }})</span>: {{ err.message }}
      </li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { api } from "@/api/client";
import { useProjects, useSubmitJob } from "@/api/queries";
import type { JobSubmitResponse } from "@/api/schema.d";

interface CompileFailure {
  kind: string;
  stage_id: string | null;
  message: string;
}

const router = useRouter();
const projects = useProjects();
const submit = useSubmitJob();

const form = reactive({
  project_slug: "",
  job_type: "fix-bug",
  title: "",
  request_text: "",
  dry_run: false,
});

const submitting = ref(false);
const errors = ref<CompileFailure[]>([]);

const canSubmit = computed(
  () =>
    form.project_slug.length > 0 &&
    form.job_type.length > 0 &&
    form.title.length > 0 &&
    form.request_text.length > 0,
);

async function handleSubmit(): Promise<void> {
  if (!canSubmit.value || submitting.value) return;
  submitting.value = true;
  errors.value = [];
  try {
    const result: JobSubmitResponse = await api.post("/jobs", { ...form });
    if (!form.dry_run) {
      await router.push({ name: "job-overview", params: { jobSlug: result.job_slug } });
    } else {
      errors.value = [
        {
          kind: "ok",
          stage_id: null,
          message: `Dry run validated. Slug would be: ${result.job_slug}`,
        },
      ];
    }
  } catch (e) {
    const msg = (e as Error).message ?? String(e);
    // Best-effort: try to parse FastAPI's structured `detail` from the message.
    errors.value = [{ kind: "submit_failed", stage_id: null, message: msg }];
  } finally {
    submitting.value = false;
  }
  void submit;
}
</script>
