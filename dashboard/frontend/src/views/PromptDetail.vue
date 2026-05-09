<template>
  <section class="space-y-5">
    <div class="flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3 min-w-0">
        <RouterLink
          :to="{ name: 'prompts' }"
          class="text-text-tertiary hover:text-text-secondary text-sm shrink-0"
          >&larr; Prompts</RouterLink
        >
        <h1 class="text-2xl font-mono font-semibold text-text-primary truncate">
          {{ name }}
        </h1>
        <span
          :class="[
            'text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full',
            source === 'bundled'
              ? 'bg-accent/10 text-accent'
              : 'bg-state-succeeded/10 text-state-succeeded',
          ]"
        >
          {{ source }}
        </span>
      </div>
      <div class="flex items-center gap-2">
        <button v-if="source === 'bundled'" @click="copyOpen = true" class="btn-secondary text-sm">
          Copy to project…
        </button>
        <RouterLink
          v-else
          :to="{ name: 'prompt-edit', params: { source, name } }"
          class="btn-accent text-sm"
        >
          Edit
        </RouterLink>
        <button
          v-if="source !== 'bundled'"
          @click="confirmDelete"
          class="btn-danger text-sm"
          :disabled="deleting"
        >
          Delete
        </button>
      </div>
    </div>

    <div class="flex items-center gap-2 text-xs">
      <button
        :class="[
          'px-3 py-1 rounded-md',
          mode === 'preview' ? 'bg-bg-elevated text-text-primary' : 'text-text-tertiary',
        ]"
        @click="mode = 'preview'"
      >
        Preview
      </button>
      <button
        :class="[
          'px-3 py-1 rounded-md',
          mode === 'source' ? 'bg-bg-elevated text-text-primary' : 'text-text-tertiary',
        ]"
        @click="mode = 'source'"
      >
        Source
      </button>
    </div>

    <div v-if="detail.isPending.value" class="text-text-tertiary">Loading…</div>
    <div v-else-if="detail.isError.value" class="text-state-failed">Failed to load prompt.</div>
    <div v-else-if="detail.data.value" class="surface p-6">
      <MarkdownView v-if="mode === 'preview'" :source="detail.data.value.content" />
      <pre v-else class="font-mono text-sm whitespace-pre-wrap text-text-primary leading-relaxed">{{
        detail.data.value.content
      }}</pre>
    </div>

    <!-- Copy-to-project modal -->
    <div
      v-if="copyOpen"
      class="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4"
      @click.self="copyOpen = false"
    >
      <div class="surface p-6 w-full max-w-md space-y-4">
        <h2 class="text-lg font-semibold text-text-primary">Copy to project</h2>
        <div class="space-y-2 text-sm">
          <label class="block text-text-tertiary">Project</label>
          <select
            v-model="copyProject"
            class="w-full bg-bg-elevated border border-border rounded-md px-2 py-2 text-text-primary"
          >
            <option v-for="p in projects.data.value ?? []" :key="p.slug" :value="p.slug">
              {{ p.slug }}
            </option>
          </select>
        </div>
        <div class="space-y-2 text-sm">
          <label class="block text-text-tertiary">Save as (rename, optional)</label>
          <input
            v-model="copyName"
            :placeholder="name"
            class="w-full bg-bg-elevated border border-border rounded-md px-2 py-2 font-mono text-text-primary"
          />
        </div>
        <div v-if="copyError" class="text-state-failed text-sm">{{ copyError }}</div>
        <div class="flex justify-end gap-2 pt-2">
          <button class="btn-secondary text-sm" @click="copyOpen = false">Cancel</button>
          <button class="btn-accent text-sm" :disabled="copying || !copyProject" @click="doCopy">
            {{ copying ? "Copying…" : "Copy" }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, toRef } from "vue";
import { RouterLink, useRouter } from "vue-router";

import {
  useCopyBundledPromptToProject,
  useDeletePrompt,
  useProjects,
  usePromptDetail,
} from "@/api/queries";
import MarkdownView from "@/components/MarkdownView.vue";

const props = defineProps<{ source: string; name: string }>();
const router = useRouter();

const sourceRef = toRef(props, "source");
const nameRef = toRef(props, "name");
const detail = usePromptDetail(
  computed(() => sourceRef.value),
  computed(() => nameRef.value),
);

const mode = ref<"preview" | "source">("preview");

const projects = useProjects();
const copyOpen = ref(false);
const copyProject = ref<string>("");
const copyName = ref("");
const copying = ref(false);
const copyError = ref("");

const copyMutation = useCopyBundledPromptToProject();
const deleteMutation = useDeletePrompt();
const deleting = computed(() => deleteMutation.isPending.value);

async function doCopy() {
  if (!copyProject.value) return;
  copying.value = true;
  copyError.value = "";
  try {
    const result = await copyMutation.mutateAsync({
      fromName: props.name,
      toProject: copyProject.value,
      toName: copyName.value || undefined,
    });
    copyOpen.value = false;
    void router.push({ name: "prompt-edit", params: { source: result.source, name: result.name } });
  } catch (err) {
    copyError.value = (err as Error).message ?? "Copy failed";
  } finally {
    copying.value = false;
  }
}

async function confirmDelete() {
  if (!window.confirm(`Delete prompt "${props.name}" from ${props.source}?`)) return;
  try {
    await deleteMutation.mutateAsync({ source: props.source, name: props.name });
    void router.push({ name: "prompts" });
  } catch (err) {
    window.alert((err as Error).message ?? "Delete failed");
  }
}
</script>
