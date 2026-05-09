<template>
  <section class="space-y-5">
    <header>
      <RouterLink
        :to="{ name: 'projects' }"
        class="text-xs text-text-secondary hover:text-text-primary"
      >
        ← Projects
      </RouterLink>
      <h1 class="text-2xl font-semibold text-text-primary mt-1">Register project</h1>
      <p class="text-sm text-text-secondary mt-1">
        Point Hammock at a local git checkout. Workflow runs will clone this repo into the job
        sandbox.
      </p>
    </header>

    <form class="space-y-4 max-w-xl" @submit.prevent="submit">
      <div>
        <label class="text-xs uppercase tracking-wider text-text-tertiary mb-2 block">
          Repository path (absolute)
        </label>
        <input
          v-model="repoPath"
          required
          placeholder="/home/you/projects/my-app"
          class="input w-full font-mono text-sm"
          @input="onPathInput"
        />
        <p class="text-[11px] text-text-tertiary mt-1">
          Must exist on this machine and contain a `.git/` directory.
        </p>
      </div>

      <div>
        <label class="text-xs uppercase tracking-wider text-text-tertiary mb-2 block">Slug</label>
        <input
          v-model="slug"
          placeholder="auto-derived from path basename"
          class="input w-full font-mono text-sm"
        />
        <p class="text-[11px] text-text-tertiary mt-1">
          Lowercase letters, digits, hyphens. Used in URLs and on disk.
        </p>
      </div>

      <div>
        <label class="text-xs uppercase tracking-wider text-text-tertiary mb-2 block">
          Display name (optional)
        </label>
        <input v-model="displayName" placeholder="Defaults to slug" class="input w-full text-sm" />
      </div>

      <div v-if="errorMessage" class="surface p-3 text-sm text-state-failed">
        {{ errorMessage }}
      </div>

      <div class="flex items-center gap-3 pt-2">
        <button
          type="submit"
          :disabled="!repoPath || mutation.isPending.value"
          class="btn-accent text-sm"
        >
          {{ mutation.isPending.value ? "Registering…" : "Register" }}
        </button>
        <RouterLink
          :to="{ name: 'projects' }"
          class="text-sm text-text-secondary hover:text-text-primary"
        >
          Cancel
        </RouterLink>
      </div>
    </form>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { RouterLink, useRouter } from "vue-router";

import { useRegisterProject } from "@/api/queries";

const router = useRouter();
const repoPath = ref("");
const slug = ref("");
const displayName = ref("");
const mutation = useRegisterProject();

const errorMessage = computed(() => {
  const e = mutation.error.value as unknown;
  if (!e) return null;
  if (typeof e === "object" && e !== null && "message" in e) {
    return (e as { message: string }).message;
  }
  return String(e);
});

function onPathInput() {
  if (slug.value === "") {
    const base = repoPath.value.split("/").filter(Boolean).pop() ?? "";
    slug.value = base
      .toLowerCase()
      .replace(/[^a-z0-9._-]+/g, "-")
      .replace(/^[-_.]+|[-_.]+$/g, "");
  }
}

async function submit() {
  const body: { repo_path: string; slug?: string; name?: string } = {
    repo_path: repoPath.value.trim(),
  };
  if (slug.value.trim()) body.slug = slug.value.trim();
  if (displayName.value.trim()) body.name = displayName.value.trim();
  try {
    const out = await mutation.mutateAsync(body);
    void router.push({ name: "project-detail", params: { slug: out.slug } });
  } catch {
    /* error rendered via mutation.error */
  }
}
</script>
