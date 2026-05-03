<template>
  <div class="p-6 max-w-2xl mx-auto space-y-8">
    <h1 class="text-2xl font-bold text-text-primary">New Job</h1>

    <div v-if="projectsError" class="text-sm text-red-400 font-mono">{{ projectsError }}</div>

    <form class="space-y-6" @submit.prevent="handleSubmit">
      <!-- Project selector -->
      <div class="space-y-1">
        <label class="block text-sm font-medium text-text-primary" for="project-select">
          Project
        </label>
        <select
          id="project-select"
          v-model="form.project_slug"
          class="w-full rounded bg-surface-secondary border border-border px-3 py-2 text-text-primary focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          <option value="" disabled>Select a project…</option>
          <option v-for="p in projects" :key="p.slug" :value="p.slug">
            {{ p.name }}
          </option>
        </select>
      </div>

      <!-- Job type -->
      <div class="space-y-1">
        <span class="block text-sm font-medium text-text-primary">Job type</span>
        <JobTypeRadio v-model="form.job_type" />
      </div>

      <!-- Title -->
      <div class="space-y-1">
        <label class="block text-sm font-medium text-text-primary" for="title-input">
          Title
        </label>
        <input
          id="title-input"
          v-model="form.title"
          type="text"
          placeholder="e.g. Fix login crash"
          class="w-full rounded bg-surface-secondary border border-border px-3 py-2 text-text-primary focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <div class="text-xs text-text-secondary mt-1">
          Slug preview: <SlugPreview :title="form.title" />
        </div>
      </div>

      <!-- Request -->
      <div class="space-y-1">
        <label class="block text-sm font-medium text-text-primary" for="request-textarea">
          Request
        </label>
        <textarea
          id="request-textarea"
          v-model="form.request_text"
          rows="6"
          placeholder="Describe what you want Hammock to do…"
          class="w-full rounded bg-surface-secondary border border-border px-3 py-2 text-text-primary focus:outline-none focus:ring-1 focus:ring-blue-500 resize-y"
        />
      </div>

      <!-- Dry-run toggle -->
      <div class="flex items-center gap-3">
        <input
          id="dry-run"
          v-model="form.dry_run"
          type="checkbox"
          class="accent-blue-500"
        />
        <label class="text-sm text-text-primary" for="dry-run">
          Dry run (preview plan without launching)
        </label>
      </div>

      <!-- Submit -->
      <button
        type="submit"
        :disabled="submitting"
        class="rounded bg-blue-600 hover:bg-blue-500 disabled:opacity-50 px-4 py-2 text-white font-medium"
      >
        {{ submitting ? "Submitting…" : form.dry_run ? "Preview plan" : "Launch job" }}
      </button>
    </form>

    <!-- Compile errors -->
    <div v-if="compileErrors.length" class="space-y-2">
      <h2 class="text-base font-semibold text-red-400">Compile errors</h2>
      <ul class="space-y-1">
        <li
          v-for="(err, i) in compileErrors"
          :key="i"
          class="text-sm text-red-300 font-mono"
        >
          <span class="font-semibold">{{ err.kind }}</span>
          <span v-if="err.stage_id"> ({{ err.stage_id }})</span>:
          {{ err.message }}
        </li>
      </ul>
    </div>

    <!-- Dry-run preview -->
    <div v-if="dryRunStages">
      <DryRunPreview :stages="dryRunStages" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import JobTypeRadio from "@/components/jobs/JobTypeRadio.vue";
import SlugPreview from "@/components/jobs/SlugPreview.vue";
import DryRunPreview from "@/components/jobs/DryRunPreview.vue";
import { api } from "@/api/client";
import type { ProjectListItem } from "@/api/schema.d";

interface CompileFailure {
  kind: string;
  stage_id: string | null;
  message: string;
}

interface JobSubmitResponse {
  job_slug: string;
  dry_run: boolean;
  stages: Array<{ id?: string; description?: string; [key: string]: unknown }> | null;
}

const router = useRouter();

const projects = ref<ProjectListItem[]>([]);
const projectsError = ref<string | null>(null);
const form = ref({
  project_slug: "",
  job_type: "fix-bug",
  title: "",
  request_text: "",
  dry_run: false,
});
const submitting = ref(false);
const compileErrors = ref<CompileFailure[]>([]);
const dryRunStages = ref<JobSubmitResponse["stages"] | null>(null);

onMounted(async () => {
  try {
    projects.value = await api.get<ProjectListItem[]>("/projects");
    if (projects.value.length && !form.value.project_slug) {
      form.value.project_slug = projects.value[0]!.slug;
    }
  } catch {
    projectsError.value = "Could not load projects. Check that the dashboard is running.";
  }
});

async function handleSubmit() {
  compileErrors.value = [];
  dryRunStages.value = null;
  submitting.value = true;

  try {
    const res = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form.value),
    });

    const body = await res.json();

    if (!res.ok) {
      const detail = body?.detail;
      if (Array.isArray(detail) && detail.length > 0 && "kind" in detail[0]) {
        // Structured compile failures: [{kind, stage_id, message}]
        compileErrors.value = detail as CompileFailure[];
      } else if (Array.isArray(detail) && detail.length > 0) {
        // FastAPI schema validation errors: [{loc, msg, type}]
        compileErrors.value = detail.map((d) => ({
          kind: "validation_error",
          stage_id: null,
          message: d.msg ?? String(d),
        }));
      } else {
        compileErrors.value = [
          { kind: "error", stage_id: null, message: String(detail ?? res.statusText) },
        ];
      }
      return;
    }

    const result = body as JobSubmitResponse;

    if (result.dry_run && result.stages) {
      dryRunStages.value = result.stages;
    } else {
      await router.push(`/jobs/${result.job_slug}`);
    }
  } finally {
    submitting.value = false;
  }
}
</script>
