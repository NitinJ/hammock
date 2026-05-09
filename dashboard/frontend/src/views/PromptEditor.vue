<template>
  <section class="space-y-4">
    <header class="flex items-center justify-between flex-wrap gap-3">
      <div class="flex items-center gap-3 min-w-0">
        <RouterLink
          :to="{ name: 'prompts' }"
          class="text-text-tertiary hover:text-text-secondary text-sm shrink-0"
          >&larr; Prompts</RouterLink
        >
        <h1 class="text-xl font-semibold text-text-primary">
          {{ isNew ? "New prompt" : `Edit ${props.name}` }}
        </h1>
      </div>
      <div class="flex items-center gap-2">
        <button class="btn-secondary text-sm" @click="cancel">Cancel</button>
        <button class="btn-accent text-sm" :disabled="saving || !canSave" @click="save">
          {{ saving ? "Saving…" : "Save" }}
        </button>
      </div>
    </header>

    <div v-if="error" class="surface p-3 text-state-failed text-sm border border-state-failed/40">
      {{ error }}
    </div>

    <div v-if="isNew" class="grid grid-cols-1 md:grid-cols-2 gap-3">
      <div class="space-y-1">
        <label class="text-text-tertiary text-xs uppercase tracking-wider">Name</label>
        <input v-model="nameDraft" class="input font-mono" placeholder="my-custom-prompt" />
      </div>
      <div class="space-y-1">
        <label class="text-text-tertiary text-xs uppercase tracking-wider">Project</label>
        <select
          v-model="projectDraft"
          class="w-full bg-bg-elevated border border-border rounded-lg px-3 py-2 text-text-primary"
        >
          <option value="" disabled>Select a project…</option>
          <option v-for="p in projects.data.value ?? []" :key="p.slug" :value="p.slug">
            {{ p.slug }}
          </option>
        </select>
      </div>
    </div>

    <div v-else class="text-sm flex items-center gap-2">
      <span class="text-text-tertiary">Saving to</span>
      <span
        class="text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-full bg-state-succeeded/10 text-state-succeeded"
        >{{ props.source }}</span
      >
      <span class="font-mono text-text-primary">{{ props.name }}</span>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 min-h-[60vh]">
      <div class="space-y-1 flex flex-col">
        <label class="text-text-tertiary text-xs uppercase tracking-wider">Source (markdown)</label>
        <textarea
          v-model="content"
          class="flex-1 min-h-[60vh] surface p-4 font-mono text-sm whitespace-pre-wrap text-text-primary leading-relaxed border-border focus:border-accent/60 focus:outline-none rounded-lg resize-y"
          spellcheck="false"
          placeholder="# My prompt&#10;&#10;Instructions to the agent…"
        ></textarea>
      </div>
      <div class="space-y-1 flex flex-col">
        <label class="text-text-tertiary text-xs uppercase tracking-wider">Preview</label>
        <div class="surface p-4 flex-1 min-h-[60vh] overflow-auto">
          <MarkdownView :source="content" />
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, toRef, watch } from "vue";
import { RouterLink, useRouter } from "vue-router";

import { useProjects, usePromptDetail, useSavePrompt } from "@/api/queries";
import MarkdownView from "@/components/MarkdownView.vue";

const props = defineProps<{
  source?: string;
  name?: string;
}>();

const router = useRouter();
const projects = useProjects();
const saveMutation = useSavePrompt();

const isNew = computed(() => !props.source || !props.name);
const sourceRef = toRef(props, "source");
const nameRef = toRef(props, "name");

const detail = usePromptDetail(
  computed(() => (isNew.value ? null : (sourceRef.value ?? null))),
  computed(() => (isNew.value ? null : (nameRef.value ?? null))),
);

const content = ref("");
const nameDraft = ref("");
const projectDraft = ref("");
const error = ref("");

watch(
  () => detail.data.value,
  (v) => {
    if (v) content.value = v.content;
  },
  { immediate: true },
);

const saving = computed(() => saveMutation.isPending.value);
const canSave = computed(() => {
  if (!content.value.trim()) return false;
  if (isNew.value) return !!nameDraft.value.trim() && !!projectDraft.value;
  return true;
});

async function save() {
  error.value = "";
  const source = isNew.value ? projectDraft.value : props.source!;
  const name = isNew.value ? nameDraft.value.trim() : props.name!;
  if (source === "bundled") {
    error.value = "Bundled prompts are read-only. Use 'Copy to project…' instead.";
    return;
  }
  try {
    await saveMutation.mutateAsync({ source, name, content: content.value });
    void router.push({ name: "prompt-detail", params: { source, name } });
  } catch (err) {
    error.value = (err as Error).message ?? "Save failed";
  }
}

function cancel() {
  if (isNew.value) {
    void router.push({ name: "prompts" });
  } else {
    void router.push({
      name: "prompt-detail",
      params: { source: props.source!, name: props.name! },
    });
  }
}
</script>
