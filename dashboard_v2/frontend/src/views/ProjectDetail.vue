<template>
  <section v-if="project.data.value" class="space-y-6">
    <header>
      <RouterLink
        :to="{ name: 'projects' }"
        class="text-xs text-text-secondary hover:text-text-primary"
      >
        ← Projects
      </RouterLink>
      <div class="flex items-center justify-between mt-1">
        <div>
          <h1 class="text-2xl font-semibold text-text-primary">{{ project.data.value.name }}</h1>
          <p class="text-xs text-text-tertiary font-mono mt-1">
            {{ project.data.value.repo_path }}
          </p>
        </div>
        <div class="flex items-center gap-2">
          <RouterLink
            :to="{ name: 'new-job', query: { project: project.data.value.slug } }"
            class="btn-accent text-sm"
          >
            Submit job
          </RouterLink>
          <button class="btn-ghost text-sm" :disabled="verify.isPending.value" @click="onVerify">
            {{ verify.isPending.value ? "Verifying…" : "Verify" }}
          </button>
          <button class="btn-ghost text-sm text-state-failed" @click="onDelete">Delete</button>
        </div>
      </div>
    </header>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
      <div class="surface p-4">
        <div class="text-xs uppercase tracking-wider text-text-tertiary mb-2">Health</div>
        <div class="flex items-center gap-2 text-sm">
          <span
            :class="[
              'size-2 rounded-full',
              project.data.value.health.path_exists && project.data.value.health.is_git_repo
                ? 'bg-state-succeeded'
                : 'bg-state-failed',
            ]"
          />
          <span class="text-text-primary">
            {{
              project.data.value.health.path_exists && project.data.value.health.is_git_repo
                ? "Reachable"
                : "Path missing or not a git repo"
            }}
          </span>
        </div>
      </div>
      <div class="surface p-4">
        <div class="text-xs uppercase tracking-wider text-text-tertiary mb-2">Default branch</div>
        <div class="text-sm font-mono text-text-primary">
          {{ project.data.value.default_branch ?? "—" }}
        </div>
      </div>
      <div class="surface p-4">
        <div class="text-xs uppercase tracking-wider text-text-tertiary mb-2">Registered</div>
        <div class="text-sm text-text-primary">
          {{ formatDate(project.data.value.registered_at) }}
        </div>
      </div>
    </div>

    <section>
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-semibold text-text-primary">Workflows</h2>
        <RouterLink
          :to="{ name: 'project-workflow-new', params: { slug: project.data.value.slug } }"
          class="btn-accent text-xs"
        >
          + New workflow
        </RouterLink>
      </div>
      <div v-if="workflows.isPending.value" class="text-text-tertiary text-sm">Loading…</div>
      <div
        v-else-if="(workflows.data.value?.workflows ?? []).length === 0"
        class="surface p-6 text-center text-text-tertiary text-sm"
      >
        No workflows for this project. Bundled workflows are still available at submit time.
      </div>
      <ul v-else class="space-y-2">
        <li
          v-for="wf in workflows.data.value?.workflows ?? []"
          :key="wf.name"
          class="surface p-3 flex items-center justify-between"
        >
          <div class="flex items-center gap-3">
            <span class="font-medium text-text-primary">{{ wf.name }}</span>
            <span
              :class="[
                'text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded',
                wf.bundled
                  ? 'bg-accent/10 text-accent'
                  : 'bg-state-succeeded/10 text-state-succeeded',
              ]"
            >
              {{ wf.bundled ? "Bundled" : "Custom" }}
            </span>
          </div>
          <RouterLink
            :to="{
              name: wf.bundled ? 'workflow-detail' : 'project-workflow-edit',
              params: wf.bundled
                ? { name: wf.name }
                : { slug: project.data.value!.slug, name: wf.name },
            }"
            class="text-xs text-text-secondary hover:text-text-primary"
          >
            {{ wf.bundled ? "View" : "Edit" }} →
          </RouterLink>
        </li>
      </ul>
    </section>
  </section>
  <div v-else-if="project.isPending.value" class="text-text-tertiary">Loading…</div>
  <div v-else class="text-state-failed">Project not found.</div>
</template>

<script setup lang="ts">
import { toRef } from "vue";
import { RouterLink, useRouter } from "vue-router";

import { useDeleteProject, useProject, useProjectWorkflows, useVerifyProject } from "@/api/queries";

const props = defineProps<{ slug: string }>();
const router = useRouter();
const slugRef = toRef(props, "slug");
const project = useProject(slugRef);
const workflows = useProjectWorkflows(slugRef);
const verify = useVerifyProject();
const del = useDeleteProject();

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

async function onVerify() {
  await verify.mutateAsync(props.slug);
}

async function onDelete() {
  if (!window.confirm(`Delete project ${props.slug}? This does not touch the repo on disk.`)) {
    return;
  }
  await del.mutateAsync(props.slug);
  void router.push({ name: "projects" });
}
</script>
