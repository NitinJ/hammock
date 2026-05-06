<template>
  <section class="mx-auto max-w-2xl space-y-6">
    <h1 class="text-xl font-semibold text-text-primary">New Job</h1>

    <form class="space-y-4" @submit.prevent="handleSubmit">
      <div class="space-y-1">
        <label class="block text-xs uppercase text-text-secondary" for="project-select">
          Project (optional — only required for code-kind workflows)
        </label>
        <select
          id="project-select"
          v-model="form.project_slug"
          class="w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-sm text-text-primary focus:border-blue-500 focus:outline-none"
        >
          <option value="">— none —</option>
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
        <select
          id="job-type"
          v-model="form.job_type"
          class="w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-sm text-text-primary focus:border-blue-500 focus:outline-none"
        >
          <option value="" disabled>Select…</option>
          <option v-for="w in workflows.data.value ?? []" :key="w.job_type" :value="w.job_type">
            {{ w.workflow_name }} ({{ w.job_type }})
          </option>
        </select>
        <p
          v-if="!workflows.isPending.value && (workflows.data.value ?? []).length === 0"
          class="text-xs text-amber-400"
        >
          No bundled workflows on disk. Add YAMLs under
          <code class="rounded bg-surface-highlight px-1">hammock/templates/workflows/</code>.
        </p>
        <p v-if="workflows.isError.value" class="text-xs text-red-400">
          Could not load workflows: {{ workflows.error.value?.message }}
        </p>
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

    <div v-if="errors.length > 0 || rawErrorBody" class="space-y-2">
      <ul
        v-if="errors.length > 0"
        class="space-y-1 rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm"
      >
        <li v-for="(err, i) in errors" :key="i" class="text-red-300">
          <span class="font-semibold">{{ err.kind }}</span>
          <span v-if="err.stage_id"> ({{ err.stage_id }})</span>: {{ err.message }}
        </li>
      </ul>
      <details v-if="rawErrorBody" class="rounded-md border border-border bg-surface-raised p-3">
        <summary class="cursor-pointer text-xs uppercase text-text-secondary">
          Raw response body
        </summary>
        <pre class="mt-2 max-h-96 overflow-auto whitespace-pre-wrap text-xs text-text-primary">{{
          rawErrorBody
        }}</pre>
      </details>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ApiError, api } from "@/api/client";
import { useProjects, useSubmitJob, useWorkflows } from "@/api/queries";
import type { JobSubmitResponse } from "@/api/schema.d";

interface CompileFailure {
  kind: string;
  stage_id: string | null;
  message: string;
}

const router = useRouter();
const projects = useProjects();
const workflows = useWorkflows();
const submit = useSubmitJob();

const form = reactive({
  project_slug: "",
  job_type: "",
  title: "",
  request_text: "",
  dry_run: false,
});

const submitting = ref(false);
const errors = ref<CompileFailure[]>([]);
const rawErrorBody = ref<string | null>(null);

// project_slug is optional (v1 derives repo from workflow when needed).
const canSubmit = computed(
  () => form.job_type.length > 0 && form.title.length > 0 && form.request_text.length > 0,
);

async function handleSubmit(): Promise<void> {
  if (!canSubmit.value || submitting.value) return;
  submitting.value = true;
  errors.value = [];
  rawErrorBody.value = null;
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
    if (e instanceof ApiError) {
      errors.value = parseDetail(e.body);
      rawErrorBody.value = formatBody(e.body, e.status, e.message);
    } else {
      const msg = (e as Error).message ?? String(e);
      errors.value = [{ kind: "submit_failed", stage_id: null, message: msg }];
      rawErrorBody.value = msg;
    }
  } finally {
    submitting.value = false;
  }
  void submit;
}

/**
 * FastAPI `detail` can be:
 *   - a list of compile failures: ``[{kind, stage_id, message}, ...]``
 *   - a list of validation errors: ``[{loc, msg, type}, ...]``
 *   - a string (HTTP 4xx with `detail="..."`)
 * Normalise to ``CompileFailure`` rows for the UI.
 */
function parseDetail(body: unknown): CompileFailure[] {
  if (!body || typeof body !== "object") {
    return [{ kind: "error", stage_id: null, message: String(body ?? "unknown error") }];
  }
  const detail = (body as { detail?: unknown }).detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => {
      if (d && typeof d === "object") {
        const o = d as Record<string, unknown>;
        if (typeof o.message === "string") {
          return {
            kind: typeof o.kind === "string" ? o.kind : "error",
            stage_id: typeof o.stage_id === "string" ? o.stage_id : null,
            message: o.message,
          };
        }
        if (typeof o.msg === "string") {
          const loc = Array.isArray(o.loc) ? o.loc.join(".") : null;
          return {
            kind: typeof o.type === "string" ? o.type : "validation_error",
            stage_id: loc,
            message: o.msg,
          };
        }
        return { kind: "error", stage_id: null, message: JSON.stringify(d) };
      }
      return { kind: "error", stage_id: null, message: String(d) };
    });
  }
  if (typeof detail === "string") {
    return [{ kind: "error", stage_id: null, message: detail }];
  }
  return [{ kind: "error", stage_id: null, message: JSON.stringify(body) }];
}

function formatBody(body: unknown, status: number, message: string): string {
  const lines: string[] = [`HTTP ${status} — ${message}`];
  if (body !== null && body !== undefined) {
    lines.push("", JSON.stringify(body, null, 2));
  }
  return lines.join("\n");
}
</script>
