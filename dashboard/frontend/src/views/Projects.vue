<template>
  <section class="space-y-4">
    <header class="flex items-center justify-between">
      <h1 class="text-xl font-semibold text-text-primary">Projects</h1>
      <RouterLink
        :to="{ name: 'project-add' }"
        class="rounded-md border border-border bg-surface-raised px-3 py-1.5 text-sm hover:bg-surface-highlight"
      >
        ＋ Add Project
      </RouterLink>
    </header>

    <p class="text-sm text-text-secondary">
      Registered local checkouts. Code-kind workflows submit against these — the engine copies the
      project directory into
      <code class="rounded bg-surface-highlight px-1">jobs/&lt;slug&gt;/repo</code>
      per job.
    </p>

    <div v-if="projects.isPending.value" class="text-text-secondary">Loading…</div>
    <div v-else-if="projects.isError.value" class="text-red-400">
      Failed to load projects: {{ projects.error.value?.message }}
    </div>
    <div
      v-else-if="!projects.data.value || projects.data.value.length === 0"
      class="rounded-md border border-border bg-surface-raised p-4 text-sm text-text-secondary"
    >
      No projects yet. Add one to start submitting code-kind workflows.
    </div>

    <table v-else class="w-full text-sm">
      <thead class="text-left text-xs uppercase text-text-secondary">
        <tr class="border-b border-border">
          <th class="py-2 pr-3">Slug</th>
          <th class="py-2 pr-3">Repo path</th>
          <th class="py-2 pr-3">Default branch</th>
          <th class="py-2 pr-3">Open jobs</th>
          <th class="py-2 pr-3">Health</th>
          <th class="py-2 pr-3"></th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="p in projects.data.value"
          :key="p.slug"
          class="cursor-pointer border-b border-border/50 hover:bg-surface-highlight"
          @click="open(p.slug)"
        >
          <td class="py-2 pr-3 font-mono text-xs text-text-primary">{{ p.slug }}</td>
          <td class="py-2 pr-3 font-mono text-xs text-text-secondary">{{ p.repo_path }}</td>
          <td class="py-2 pr-3 text-text-secondary">{{ p.default_branch ?? "—" }}</td>
          <td class="py-2 pr-3 text-text-secondary">{{ p.open_jobs }}</td>
          <td class="py-2 pr-3">
            <span
              v-if="p.last_health_check_status"
              :class="healthClass(p.last_health_check_status)"
              class="rounded-md px-2 py-0.5 text-xs font-medium ring-1 ring-inset"
            >
              {{ p.last_health_check_status }}
            </span>
            <span v-else class="text-text-secondary">—</span>
          </td>
          <td class="py-2 pr-3 text-right">
            <button
              type="button"
              class="rounded-md border border-red-500/40 bg-red-500/10 px-2 py-0.5 text-xs text-red-300 hover:bg-red-500/20"
              :disabled="deletingSlug === p.slug"
              @click.stop="confirmDelete(p.slug)"
            >
              {{ deletingSlug === p.slug ? "…" : "Delete" }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<script setup lang="ts">
import { ref } from "vue";
import { RouterLink, useRouter } from "vue-router";
import { useDeleteProject, useProjects } from "@/api/queries";
import type { HealthCheckStatus } from "@/api/schema.d";

const router = useRouter();
const projects = useProjects();
const deleteProject = useDeleteProject();
const deletingSlug = ref<string | null>(null);

function open(slug: string): void {
  router.push({ name: "project-detail", params: { slug } });
}

async function confirmDelete(slug: string): Promise<void> {
  if (
    !window.confirm(`Delete project ${slug}? This removes the registration; jobs are unaffected.`)
  )
    return;
  deletingSlug.value = slug;
  try {
    await deleteProject.mutateAsync(slug);
  } finally {
    deletingSlug.value = null;
  }
}

function healthClass(status: HealthCheckStatus): string {
  if (status === "pass") return "bg-green-500/20 text-green-300 ring-green-500/30";
  if (status === "warn") return "bg-amber-500/20 text-amber-300 ring-amber-500/30";
  return "bg-red-500/20 text-red-300 ring-red-500/30";
}
</script>
