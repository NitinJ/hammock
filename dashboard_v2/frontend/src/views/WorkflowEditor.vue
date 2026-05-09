<template>
  <section class="space-y-5">
    <header class="surface p-5">
      <div class="flex items-center justify-between mb-3">
        <RouterLink :to="backRoute" class="text-xs text-text-tertiary hover:text-text-secondary">
          ← {{ backLabel }}
        </RouterLink>
        <div class="flex items-center gap-2">
          <button class="btn-ghost text-sm" @click="showYaml = !showYaml">
            {{ showYaml ? "Graph view" : "YAML view" }}
          </button>
          <button class="btn-ghost text-sm" :disabled="!liveNodes" @click="onAddNode">
            + Node
          </button>
          <button
            type="button"
            class="btn-accent text-sm"
            :disabled="!canSave || saveBusy"
            @click="onSave"
          >
            {{ saveBusy ? "Saving…" : "Save" }}
          </button>
        </div>
      </div>
      <div v-if="projectSlug" class="mb-2 text-[11px] uppercase tracking-wider text-accent">
        project: {{ projectSlug }}
      </div>
      <div v-if="isCreate" class="space-y-1.5">
        <label for="wf-name" class="text-xs uppercase tracking-wider text-text-tertiary">
          Name
        </label>
        <input
          id="wf-name"
          v-model="newName"
          type="text"
          placeholder="my-workflow"
          class="input font-mono text-sm w-full max-w-md"
        />
      </div>
      <div v-else>
        <h1 class="font-semibold text-2xl text-text-primary">Edit · {{ name }}</h1>
      </div>
    </header>

    <div v-if="loadError" class="surface bg-state-failed/10 border-state-failed/40 p-4">
      <p class="text-sm text-state-failed">{{ loadError }}</p>
    </div>

    <div class="grid grid-cols-12 gap-5">
      <!-- Left: editor or yaml -->
      <div class="col-span-12 lg:col-span-7 surface p-3 flex flex-col">
        <div
          v-if="!showYaml"
          class="text-xs uppercase tracking-wider text-text-tertiary px-2 py-1 mb-2"
        >
          DAG · click a node to edit
        </div>
        <div v-else class="text-xs uppercase tracking-wider text-text-tertiary px-2 py-1 mb-2">
          YAML
        </div>
        <textarea
          v-if="showYaml"
          v-model="yamlText"
          spellcheck="false"
          class="input font-mono text-xs flex-1 min-h-[55vh] resize-y"
        />
        <div v-else class="flex-1 overflow-auto">
          <DagVisualizer
            v-if="liveNodes && liveNodes.length > 0"
            :nodes="liveNodes"
            selectable
            :selected-id="selectedNodeId"
            @select="onNodeSelect"
          />
          <p v-else class="text-text-tertiary text-xs p-4">Add a node to render the DAG.</p>
        </div>
        <p
          v-if="liveError"
          class="text-state-failed text-xs px-2 mt-2 font-mono whitespace-pre-wrap"
        >
          {{ liveError }}
        </p>
        <p v-else-if="livePending" class="text-text-tertiary text-xs px-2 mt-2">Validating…</p>
        <p v-else-if="liveNodes" class="text-text-tertiary text-xs px-2 mt-2">
          {{ liveNodes.length }} node{{ liveNodes.length === 1 ? "" : "s" }} · ready to save
        </p>
        <p v-if="saveError" class="text-state-failed text-xs px-2 mt-2 font-mono">
          {{ saveError }}
        </p>
      </div>

      <!-- Right: node side panel -->
      <div class="col-span-12 lg:col-span-5 surface p-4 flex flex-col">
        <div v-if="selectedNode" class="space-y-3">
          <div class="flex items-center justify-between">
            <h3 class="font-semibold text-text-primary">Edit node</h3>
            <button class="text-xs text-state-failed hover:underline" @click="onDeleteNode">
              Delete
            </button>
          </div>
          <div>
            <label class="text-xs uppercase tracking-wider text-text-tertiary block mb-1">
              ID
            </label>
            <input v-model="editId" class="input font-mono text-sm w-full" />
          </div>
          <div>
            <div class="flex items-center justify-between mb-1">
              <label class="text-xs uppercase tracking-wider text-text-tertiary"> Prompt </label>
              <RouterLink
                :to="{ name: 'prompt-new' }"
                target="_blank"
                rel="noopener"
                class="text-[11px] text-accent hover:underline"
                title="Open the prompt editor in a new tab"
              >
                + New prompt
              </RouterLink>
            </div>
            <select v-model="editPrompt" class="input text-sm w-full">
              <option v-for="p in promptOptions" :key="p" :value="p">{{ p }}</option>
            </select>
          </div>
          <div>
            <label class="flex items-center gap-2 text-sm text-text-primary">
              <input v-model="editHumanReview" type="checkbox" class="size-4" />
              Human review (job pauses, operator approves)
            </label>
          </div>
          <div>
            <label class="text-xs uppercase tracking-wider text-text-tertiary block mb-1">
              After (deps; comma-separated)
            </label>
            <input v-model="editAfter" class="input font-mono text-xs w-full" />
          </div>
          <div>
            <label class="text-xs uppercase tracking-wider text-text-tertiary block mb-1">
              Requires (comma-separated paths)
            </label>
            <input v-model="editRequires" class="input font-mono text-xs w-full" />
          </div>
          <div>
            <label class="text-xs uppercase tracking-wider text-text-tertiary block mb-1">
              Description
            </label>
            <textarea v-model="editDescription" rows="3" class="input text-xs w-full"></textarea>
          </div>
          <div v-if="projectSlug" class="pt-2 border-t border-border">
            <label class="text-xs uppercase tracking-wider text-text-tertiary block mb-1">
              Prompt content (saves as project prompt)
            </label>
            <textarea
              v-model="editPromptContent"
              rows="6"
              class="input font-mono text-xs w-full"
              placeholder="Edit the prompt content; saving creates a project-local prompt with the same name."
            ></textarea>
            <button
              class="btn-ghost text-xs mt-1"
              :disabled="!editPromptContent.trim() || promptSaveBusy"
              @click="onSavePrompt"
            >
              {{ promptSaveBusy ? "Saving prompt…" : "Save prompt to project" }}
            </button>
          </div>
          <div class="pt-2 flex items-center gap-2">
            <button class="btn-accent text-xs" @click="onApplyNodeEdits">Apply to YAML</button>
            <button class="btn-ghost text-xs" @click="onCloseSidebar">Cancel</button>
          </div>
        </div>
        <div v-else class="text-xs text-text-tertiary p-4">
          {{ showYaml ? "YAML view active." : "Click a node in the DAG to edit it inline." }}
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, toRef, watch } from "vue";
import { RouterLink, useRouter, useRoute } from "vue-router";

import DagVisualizer from "@/components/workflow/DagVisualizer.vue";
import {
  useCreateWorkflow,
  useProjectPrompts,
  useSaveProjectPrompt,
  useSaveProjectWorkflow,
  useUpdateProjectWorkflow,
  useUpdateWorkflow,
  useWorkflow,
  validateWorkflowYaml,
} from "@/api/queries";
import type { WorkflowNode } from "@/api/types";

const props = defineProps<{ name?: string; projectSlug?: string }>();
const router = useRouter();
const route = useRoute();

const isCreate = computed(() => !props.name);
const name = computed(() => props.name ?? "");
const projectSlug = computed(() => props.projectSlug ?? "");

const newName = ref<string>("");
const yamlText = ref<string>("");
const loadError = ref<string | null>(null);
const saveError = ref<string | null>(null);
const saveBusy = ref(false);
const showYaml = ref(false);

const liveNodes = ref<WorkflowNode[] | null>(null);
const liveError = ref<string | null>(null);
const livePending = ref(false);

const selectedNodeId = ref<string | null>(null);
const selectedNode = computed<WorkflowNode | null>(() => {
  if (!selectedNodeId.value || !liveNodes.value) return null;
  return liveNodes.value.find((n) => n.id === selectedNodeId.value) ?? null;
});

// Edit-form mirrors of the selected node
const editId = ref("");
const editPrompt = ref("");
const editHumanReview = ref(false);
const editAfter = ref("");
const editRequires = ref("");
const editDescription = ref("");
const editPromptContent = ref("");
const promptSaveBusy = ref(false);

const initialLoad = useWorkflow(name);
const create = useCreateWorkflow();
const update = useUpdateWorkflow();
const saveProjectWf = useSaveProjectWorkflow(projectSlug);
const updateProjectWf = useUpdateProjectWorkflow(projectSlug);
const projectPrompts = useProjectPrompts(toRef(() => projectSlug.value));
const saveProjectPrompt = useSaveProjectPrompt(toRef(() => projectSlug.value));

const promptOptions = computed(() => {
  const list = projectPrompts.data.value?.prompts ?? [];
  if (list.length > 0) return list.map((p) => p.name);
  // Fallback to bundled defaults
  return [
    "write-bug-report",
    "write-design-spec",
    "review",
    "write-impl-spec",
    "implement",
    "pr-create",
    "write-summary",
  ];
});

const backRoute = computed(() => {
  if (projectSlug.value) {
    return { name: "project-detail", params: { slug: projectSlug.value } };
  }
  return isCreate.value
    ? { name: "workflows" }
    : { name: "workflow-detail", params: { name: name.value } };
});

const backLabel = computed(() => {
  if (projectSlug.value) return projectSlug.value;
  return isCreate.value ? "Workflows" : name.value;
});

const STARTER_YAML = `name: my-workflow
description: |
  Describe what this workflow does.

nodes:
  - id: write-bug-report
    prompt: write-bug-report
    requires:
      - output.md

  - id: write-design-spec
    prompt: write-design-spec
    after: [write-bug-report]
    requires:
      - output.md
`;

onMounted(async () => {
  if (isCreate.value) {
    const copyFrom = route.query.copy_from;
    if (typeof copyFrom === "string" && copyFrom.length > 0) {
      try {
        const r = await fetch(`/api/workflows/${encodeURIComponent(copyFrom)}`);
        if (r.ok) {
          const detail = (await r.json()) as { yaml: string };
          yamlText.value = detail.yaml;
        } else {
          yamlText.value = STARTER_YAML;
        }
      } catch {
        yamlText.value = STARTER_YAML;
      }
    } else {
      yamlText.value = STARTER_YAML;
    }
  } else if (projectSlug.value && name.value) {
    // Load via project endpoint to honor project-local override
    try {
      const r = await fetch(
        `/api/projects/${encodeURIComponent(projectSlug.value)}/workflows/${encodeURIComponent(name.value)}`,
      );
      if (r.ok) {
        const detail = (await r.json()) as { yaml: string };
        yamlText.value = detail.yaml;
      }
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e);
    }
  }
});

watch(
  () => initialLoad.data.value,
  (val) => {
    if (!isCreate.value && !projectSlug.value && val && !yamlText.value) {
      yamlText.value = val.yaml;
    }
  },
);

const canSave = computed(() => {
  if (!yamlText.value.trim()) return false;
  if (isCreate.value && !newName.value.trim()) return false;
  if (liveError.value) return false;
  return true;
});

let validationTimer: ReturnType<typeof setTimeout> | null = null;

watch(
  yamlText,
  (val) => {
    if (validationTimer) clearTimeout(validationTimer);
    if (!val.trim()) {
      liveNodes.value = null;
      liveError.value = null;
      livePending.value = false;
      return;
    }
    livePending.value = true;
    validationTimer = setTimeout(async () => {
      try {
        const r = await validateWorkflowYaml(val);
        if (r.valid && r.nodes) {
          liveNodes.value = r.nodes;
          liveError.value = null;
        } else {
          liveError.value = r.error ?? "invalid";
        }
      } catch (e) {
        liveError.value = e instanceof Error ? e.message : String(e);
      } finally {
        livePending.value = false;
      }
    }, 500);
  },
  { immediate: true },
);

function onNodeSelect(id: string): void {
  selectedNodeId.value = id;
  const node = liveNodes.value?.find((n) => n.id === id);
  if (!node) return;
  editId.value = node.id;
  editPrompt.value = node.prompt;
  editHumanReview.value = node.human_review;
  editAfter.value = node.after.join(", ");
  editRequires.value = (node.requires ?? ["output.md"]).join(", ");
  editDescription.value = node.description ?? "";
  editPromptContent.value = "";
  // Lazily fetch the prompt content
  if (projectSlug.value) {
    void fetch(
      `/api/projects/${encodeURIComponent(projectSlug.value)}/prompts/${encodeURIComponent(node.prompt)}`,
    )
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && typeof d.content === "string") editPromptContent.value = d.content;
      })
      .catch(() => {});
  }
}

function onCloseSidebar(): void {
  selectedNodeId.value = null;
}

function onAddNode(): void {
  // Append a fresh node to the YAML
  const id = `new-node-${Math.random().toString(36).slice(2, 6)}`;
  const stub = `\n  - id: ${id}\n    prompt: write-bug-report\n    requires:\n      - output.md\n`;
  yamlText.value = yamlText.value.trimEnd() + stub;
}

function onDeleteNode(): void {
  if (!selectedNodeId.value || !liveNodes.value) return;
  const remaining = liveNodes.value.filter((n) => n.id !== selectedNodeId.value);
  yamlText.value = serializeNodes(remaining, headerYaml());
  selectedNodeId.value = null;
}

function onApplyNodeEdits(): void {
  if (!selectedNodeId.value || !liveNodes.value) return;
  const updated = liveNodes.value.map((n) => {
    if (n.id !== selectedNodeId.value) return n;
    return {
      ...n,
      id: editId.value.trim() || n.id,
      prompt: editPrompt.value || n.prompt,
      human_review: editHumanReview.value,
      after: editAfter.value
        .split(",")
        .map((s) => s.trim())
        .filter((s) => !!s),
      requires: editRequires.value
        .split(",")
        .map((s) => s.trim())
        .filter((s) => !!s),
      description: editDescription.value || null,
    };
  });
  yamlText.value = serializeNodes(updated, headerYaml());
  selectedNodeId.value = editId.value.trim() || selectedNodeId.value;
}

async function onSavePrompt(): Promise<void> {
  if (!projectSlug.value || !editPrompt.value || !editPromptContent.value.trim()) return;
  promptSaveBusy.value = true;
  try {
    await saveProjectPrompt.mutateAsync({
      name: editPrompt.value,
      content: editPromptContent.value,
    });
  } finally {
    promptSaveBusy.value = false;
  }
}

function headerYaml(): string {
  // Re-extract the top of the YAML up to "nodes:"
  const idx = yamlText.value.indexOf("\nnodes:");
  if (idx < 0) return `name: ${isCreate.value ? newName.value || "my-workflow" : name.value}\n`;
  return yamlText.value.slice(0, idx + 1);
}

function serializeNodes(nodes: WorkflowNode[], header: string): string {
  const out: string[] = [header.trimEnd(), "", "nodes:"];
  for (const n of nodes) {
    out.push(`  - id: ${n.id}`);
    out.push(`    prompt: ${n.prompt}`);
    if (n.after && n.after.length > 0) {
      out.push(`    after: [${n.after.join(", ")}]`);
    }
    if (n.human_review) out.push(`    human_review: true`);
    const reqs = n.requires ?? ["output.md"];
    out.push(`    requires:`);
    for (const r of reqs) out.push(`      - ${r}`);
    if (n.description) {
      out.push(`    description: |`);
      for (const line of n.description.split("\n")) out.push(`      ${line}`);
    }
    out.push("");
  }
  return out.join("\n");
}

async function onSave(): Promise<void> {
  saveError.value = null;
  saveBusy.value = true;
  try {
    if (projectSlug.value) {
      // Project-scoped save
      const targetName = isCreate.value ? newName.value.trim() : name.value;
      try {
        await saveProjectWf.mutateAsync({ name: targetName, yaml: yamlText.value });
      } catch (e) {
        // If 409 (exists), fall back to update
        const msg = e instanceof Error ? e.message : String(e);
        if (msg.includes("already exists")) {
          await updateProjectWf.mutateAsync({ name: targetName, yaml: yamlText.value });
        } else {
          throw e;
        }
      }
      void router.push({ name: "project-detail", params: { slug: projectSlug.value } });
    } else if (isCreate.value) {
      const created = await create.mutateAsync({
        name: newName.value.trim(),
        yaml: yamlText.value,
      });
      void router.push({ name: "workflow-detail", params: { name: created.name } });
    } else {
      const updated = await update.mutateAsync({
        name: name.value,
        yaml: yamlText.value,
      });
      void router.push({ name: "workflow-detail", params: { name: updated.name } });
    }
  } catch (e) {
    saveError.value = e instanceof Error ? e.message : String(e);
  } finally {
    saveBusy.value = false;
  }
}
</script>
