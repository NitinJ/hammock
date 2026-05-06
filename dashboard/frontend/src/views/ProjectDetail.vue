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
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";
import { useDeleteProject, useProject, useReverifyProject } from "@/api/queries";
import type { RegisterProjectResponse } from "@/api/schema.d";

const route = useRoute();
const router = useRouter();
const slug = computed(() => String(route.params.slug ?? ""));

const project = useProject(slug);
const reverify = useReverifyProject();
const deleteProject = useDeleteProject();

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
