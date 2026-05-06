<template>
  <section class="mx-auto max-w-2xl space-y-6">
    <header>
      <RouterLink :to="{ name: 'projects' }" class="text-xs text-text-secondary hover:underline">
        ← Projects
      </RouterLink>
      <h1 class="mt-1 text-xl font-semibold text-text-primary">Add Project</h1>
      <p class="mt-1 text-sm text-text-secondary">
        Register a local git checkout. Hammock copies it into
        <code class="rounded bg-surface-highlight px-1">jobs/&lt;slug&gt;/repo</code> per job —
        <code class="rounded bg-surface-highlight px-1">.env</code> and other untracked files come
        along. Per-job clones from a remote URL are not supported.
      </p>
    </header>

    <form class="space-y-4" @submit.prevent="handleSubmit">
      <div class="space-y-1">
        <label class="block text-xs uppercase text-text-secondary" for="path">
          Absolute path to local checkout
        </label>
        <input
          id="path"
          v-model="form.path"
          type="text"
          class="w-full rounded-md border border-border bg-surface-raised px-3 py-2 font-mono text-sm text-text-primary focus:border-blue-500 focus:outline-none"
          placeholder="/home/you/code/my-app"
          autocomplete="off"
        />
      </div>

      <div class="space-y-1">
        <label class="block text-xs uppercase text-text-secondary" for="slug">
          Slug (optional)
        </label>
        <input
          id="slug"
          v-model="form.slug"
          type="text"
          class="w-full rounded-md border border-border bg-surface-raised px-3 py-2 font-mono text-sm text-text-primary focus:border-blue-500 focus:outline-none"
          placeholder="auto-derived from folder name"
        />
      </div>

      <div class="space-y-1">
        <label class="block text-xs uppercase text-text-secondary" for="name">
          Name (optional)
        </label>
        <input
          id="name"
          v-model="form.name"
          type="text"
          class="w-full rounded-md border border-border bg-surface-raised px-3 py-2 text-sm text-text-primary focus:border-blue-500 focus:outline-none"
          placeholder="auto-derived from folder name"
        />
      </div>

      <button
        type="submit"
        :disabled="!canSubmit || submitting"
        class="rounded-md border border-blue-500 bg-blue-500/20 px-3 py-1.5 text-sm text-blue-200 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {{ submitting ? "Registering…" : "Add" }}
      </button>
    </form>

    <div
      v-if="result"
      class="rounded-md border border-border bg-surface-raised p-3 text-sm"
      :class="resultClass"
    >
      <div class="font-semibold">Verify: {{ result.verify.status }}</div>
      <div v-if="result.verify.remote_url" class="text-xs text-text-secondary">
        remote_url: {{ result.verify.remote_url }}
      </div>
      <div v-if="result.verify.default_branch" class="text-xs text-text-secondary">
        default_branch: {{ result.verify.default_branch }}
      </div>
      <div v-if="result.verify.reason" class="mt-1 text-xs">{{ result.verify.reason }}</div>
    </div>

    <div
      v-if="errorBody"
      class="space-y-2 rounded-md border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300"
    >
      <div class="font-semibold">Failed to register</div>
      <div class="whitespace-pre-wrap text-xs">{{ errorBody }}</div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { ApiError } from "@/api/client";
import { useRegisterProject } from "@/api/queries";
import type { RegisterProjectResponse } from "@/api/schema.d";

const router = useRouter();
const register = useRegisterProject();

const form = reactive({
  path: "",
  slug: "",
  name: "",
});
const submitting = ref(false);
const result = ref<RegisterProjectResponse | null>(null);
const errorBody = ref<string | null>(null);

const canSubmit = computed(() => form.path.trim().length > 0);

const resultClass = computed(() => {
  if (!result.value) return "";
  const s = result.value.verify.status;
  if (s === "pass") return "border-green-500/40 bg-green-500/10 text-green-200";
  if (s === "warn") return "border-amber-500/40 bg-amber-500/10 text-amber-200";
  return "border-red-500/40 bg-red-500/10 text-red-300";
});

async function handleSubmit(): Promise<void> {
  if (!canSubmit.value || submitting.value) return;
  submitting.value = true;
  result.value = null;
  errorBody.value = null;
  try {
    const body = {
      path: form.path.trim(),
      ...(form.slug.trim() ? { slug: form.slug.trim() } : {}),
      ...(form.name.trim() ? { name: form.name.trim() } : {}),
    };
    const res = await register.mutateAsync(body);
    result.value = res;
    // Navigate to the detail page on success.
    await router.push({ name: "project-detail", params: { slug: res.project.slug } });
  } catch (e) {
    if (e instanceof ApiError) {
      const detail = (e.body as { detail?: unknown })?.detail;
      errorBody.value =
        typeof detail === "string" ? detail : JSON.stringify(detail ?? e.body, null, 2);
    } else {
      errorBody.value = (e as Error).message;
    }
  } finally {
    submitting.value = false;
  }
}
</script>
