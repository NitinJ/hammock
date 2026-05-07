<template>
  <section class="space-y-4">
    <header class="flex items-start justify-between gap-3">
      <div>
        <RouterLink :to="{ name: 'projects' }" class="text-xs text-text-secondary hover:underline">
          ← Projects
        </RouterLink>
        <h1 class="mt-1 font-mono text-lg font-semibold text-text-primary">{{ slug }}</h1>
      </div>
      <div class="flex gap-2">
        <button
          type="button"
          :disabled="reverify.isPending.value"
          class="rounded-md border border-border bg-surface-raised px-3 py-1.5 text-sm hover:bg-surface-highlight disabled:opacity-50"
          @click="onReverify"
        >
          {{ reverify.isPending.value ? "Verifying…" : "Re-verify" }}
        </button>
        <button
          type="button"
          :disabled="deleting"
          class="rounded-md border border-red-500/40 bg-red-500/10 px-3 py-1.5 text-sm text-red-300 hover:bg-red-500/20 disabled:opacity-50"
          @click="onDelete"
        >
          {{ deleting ? "Deleting…" : "Delete" }}
        </button>
      </div>
    </header>

    <div v-if="project.isPending.value" class="text-text-secondary">Loading…</div>
    <div v-else-if="project.isError.value" class="text-red-400">
      {{ project.error.value?.message }}
    </div>

    <dl
      v-else-if="project.data.value"
      class="grid grid-cols-1 gap-3 rounded-md border border-border bg-surface-raised p-4 text-sm sm:grid-cols-2"
    >
      <div>
        <dt class="text-xs uppercase text-text-secondary">Name</dt>
        <dd class="text-text-primary">{{ project.data.value.name }}</dd>
      </div>
      <div>
        <dt class="text-xs uppercase text-text-secondary">Repo path</dt>
        <dd class="font-mono text-xs text-text-primary">{{ project.data.value.repo_path }}</dd>
      </div>
      <div>
        <dt class="text-xs uppercase text-text-secondary">Remote URL</dt>
        <dd class="font-mono text-xs text-text-primary">
          {{ project.data.value.remote_url ?? "—" }}
        </dd>
      </div>
      <div>
        <dt class="text-xs uppercase text-text-secondary">Default branch</dt>
        <dd class="font-mono text-xs text-text-primary">{{ project.data.value.default_branch }}</dd>
      </div>
      <div>
        <dt class="text-xs uppercase text-text-secondary">Last verify</dt>
        <dd class="text-xs text-text-primary">
          <span v-if="project.data.value.last_health_check_status">
            {{ project.data.value.last_health_check_status }} ·
            {{ formatDate(project.data.value.last_health_check_at) }}
          </span>
          <span v-else class="text-text-secondary">never</span>
        </dd>
      </div>
    </dl>

    <div
      v-if="lastReverify"
      class="rounded-md border border-border bg-surface-raised p-3 text-sm"
      :class="reverifyClass"
    >
      <div class="font-semibold">Verify: {{ lastReverify.verify.status }}</div>
      <div v-if="lastReverify.verify.reason" class="mt-1 text-xs">
        {{ lastReverify.verify.reason }}
      </div>
    </div>

    <!-- Stage 6 — Workflows section. Lists bundled + project-local
         for this project. Each bundled entry without a project-local
         counterpart shows a "Copy to project" button. -->
    <section class="rounded-md border border-border bg-surface-raised p-4">
      <header class="mb-3 flex items-center justify-between">
        <h2 class="text-sm font-semibold uppercase tracking-wide text-text-secondary">Workflows</h2>
        <span v-if="workflows.isPending.value" class="text-xs text-text-secondary">Loading…</span>
      </header>

      <div
        v-if="copyError"
        class="mb-2 rounded-md border border-red-500/40 bg-red-500/10 p-2 text-xs text-red-300"
      >
        {{ copyError }}
      </div>

      <ul v-if="workflows.data.value" class="divide-y divide-border/50">
        <li
          v-for="w in workflows.data.value"
          :key="`${w.source}/${w.job_type}`"
          class="flex items-center justify-between gap-3 py-2 text-sm"
        >
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="font-mono text-text-primary">{{ w.job_type }}</span>
              <span
                class="rounded-full px-2 py-0.5 text-xs"
                :class="
                  w.source === 'custom'
                    ? 'bg-blue-500/20 text-blue-300'
                    : 'bg-surface text-text-secondary'
                "
              >
                {{ w.source }}
              </span>
              <span
                v-if="!w.valid"
                class="rounded-full bg-amber-500/20 px-2 py-0.5 text-xs text-amber-300"
              >
                invalid
              </span>
            </div>
            <div v-if="w.workflow_name" class="text-xs text-text-secondary">
              {{ w.workflow_name }}
            </div>
            <div v-if="!w.valid && w.error" class="mt-1 text-xs text-amber-300">
              {{ w.error }}
            </div>
          </div>
          <button
            v-if="w.source === 'bundled' && canCopy(w.job_type)"
            type="button"
            :disabled="copying === w.job_type"
            class="shrink-0 rounded-md border border-blue-500/40 bg-blue-500/10 px-3 py-1 text-xs text-blue-200 hover:bg-blue-500/20 disabled:opacity-50"
            @click="onCopy(w.job_type)"
          >
            {{ copying === w.job_type ? "Copying…" : "Copy to project" }}
          </button>
        </li>
      </ul>
      <p v-if="workflows.isError.value" class="text-xs text-red-400">
        Could not load workflows: {{ workflows.error.value?.message }}
      </p>

      <p
        v-if="lastCopiedTo"
        class="mt-3 rounded-md border border-blue-500/40 bg-blue-500/5 p-2 text-xs text-blue-200"
      >
        Copied to <code class="font-mono">{{ lastCopiedTo }}</code
        >. Commit it via your normal git workflow to track it.
      </p>
    </section>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { ApiError } from "@/api/client";
import {
  useCopyWorkflow,
  useDeleteProject,
  useProject,
  useProjectWorkflows,
  useReverifyProject,
} from "@/api/queries";
import type { RegisterProjectResponse } from "@/api/schema.d";

const route = useRoute();
const router = useRouter();
const slug = computed(() => String(route.params.slug ?? ""));

const project = useProject(slug);
const reverify = useReverifyProject();
const deleteProject = useDeleteProject();
const workflows = useProjectWorkflows(slug);
const copyMutation = useCopyWorkflow(slug);

const copying = ref<string | null>(null);
const copyError = ref<string | null>(null);
const lastCopiedTo = ref<string | null>(null);

/** Whether a "Copy to project" button should appear next to a
 *  bundled workflow. Hide when the project already has a copy under
 *  the default suffix (`<job_type>-<slug>`) — clicking again would
 *  409. The operator can still copy with an explicit dest_name from
 *  the API directly. */
function canCopy(jobType: string): boolean {
  const list = workflows.data.value ?? [];
  const expectedDest = `${jobType}-${slug.value}`;
  return !list.some((w) => w.source === "custom" && w.job_type === expectedDest);
}

async function onCopy(jobType: string): Promise<void> {
  copying.value = jobType;
  copyError.value = null;
  try {
    const resp = await copyMutation.mutateAsync({ source: jobType });
    lastCopiedTo.value = resp.destination;
  } catch (e) {
    if (e instanceof ApiError) {
      const detail =
        e.body && typeof e.body === "object" && "detail" in (e.body as Record<string, unknown>)
          ? String((e.body as Record<string, unknown>).detail)
          : e.message;
      copyError.value = detail;
    } else {
      copyError.value = (e as Error).message ?? String(e);
    }
  } finally {
    copying.value = null;
  }
}

const deleting = ref(false);
const lastReverify = ref<RegisterProjectResponse | null>(null);

const reverifyClass = computed(() => {
  const s = lastReverify.value?.verify.status;
  if (s === "pass") return "border-green-500/40 bg-green-500/10 text-green-200";
  if (s === "warn") return "border-amber-500/40 bg-amber-500/10 text-amber-200";
  if (s === "fail") return "border-red-500/40 bg-red-500/10 text-red-300";
  return "";
});

async function onReverify(): Promise<void> {
  lastReverify.value = await reverify.mutateAsync(slug.value);
}

async function onDelete(): Promise<void> {
  if (!window.confirm(`Delete project ${slug.value}?`)) return;
  deleting.value = true;
  try {
    await deleteProject.mutateAsync(slug.value);
    await router.push({ name: "projects" });
  } finally {
    deleting.value = false;
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}
</script>
