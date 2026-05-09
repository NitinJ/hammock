<template>
  <section class="flex flex-col h-full">
    <header class="px-4 py-3 border-b border-border flex items-center justify-between">
      <div>
        <h3 class="font-semibold text-text-primary text-sm">Builder agent</h3>
        <p class="text-[11px] text-text-tertiary">
          Talk through the workflow design. Apply proposals into the editor.
        </p>
      </div>
      <button
        v-if="sessionId"
        type="button"
        class="text-[11px] text-text-tertiary hover:text-text-secondary"
        title="Close panel"
        @click="$emit('close')"
      >
        ✕
      </button>
    </header>

    <div ref="scrollHost" class="flex-1 overflow-auto px-4 py-3 space-y-3">
      <div v-if="!sessionId" class="text-text-tertiary text-sm">Starting session…</div>
      <p
        v-else-if="(session.data.value?.messages ?? []).length === 0"
        class="text-text-tertiary text-sm"
      >
        Say what you want to build. The agent will ask clarifying questions or propose a yaml.
      </p>
      <ul v-else class="space-y-3">
        <li
          v-for="m in session.data.value?.messages ?? []"
          :key="m.id"
          :class="['flex', m.from === 'user' ? 'justify-end' : 'justify-start']"
        >
          <div
            :class="[
              'max-w-[85%] surface p-3 text-sm',
              m.from === 'user' ? 'bg-accent/10 border-accent/40' : '',
            ]"
          >
            <div class="text-[10px] uppercase tracking-wider text-text-tertiary mb-1">
              {{ m.from }} · {{ formatAt(m.timestamp) }}
            </div>
            <MarkdownView :source="m.text" />
            <button
              v-if="m.proposed_yaml"
              type="button"
              class="btn-accent text-xs mt-2"
              :disabled="applying"
              @click="onApply(m)"
            >
              {{ applying && applyingId === m.id ? "Applying…" : "Apply to editor" }}
            </button>
          </div>
        </li>
      </ul>
      <p v-if="send.isPending.value" class="text-text-tertiary text-xs italic">
        Builder agent thinking…
      </p>
      <p v-if="errorMsg" class="text-state-failed text-xs">{{ errorMsg }}</p>
    </div>

    <form class="border-t border-border p-3 flex items-end gap-2" @submit.prevent="onSend">
      <textarea
        v-model="draft"
        rows="2"
        placeholder="Describe what you want, ask for revisions, or paste an example."
        class="input font-mono text-xs flex-1"
        :disabled="!sessionId || send.isPending.value"
      ></textarea>
      <button
        type="submit"
        class="btn-accent text-sm shrink-0"
        :disabled="!sessionId || !draft.trim() || send.isPending.value"
      >
        {{ send.isPending.value ? "…" : "Send" }}
      </button>
    </form>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, toRef, watch } from "vue";

import MarkdownView from "@/components/MarkdownView.vue";
import {
  useApplyBuilderProposal,
  useBuilderSession,
  useCreateBuilderSession,
  useSendBuilderMessage,
  type BuilderMessage,
} from "@/api/queries";

const props = defineProps<{
  projectSlug?: string | null;
  workflowName?: string | null;
  startingYaml?: string;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "applied", yaml: string): void;
}>();

const sessionId = ref<string | null>(null);
const draft = ref("");
const errorMsg = ref<string | null>(null);
const applyingId = ref<string | null>(null);

const sessionIdRef = toRef(sessionId);
const create = useCreateBuilderSession();
const session = useBuilderSession(sessionIdRef);
const send = useSendBuilderMessage(sessionIdRef);
const apply = useApplyBuilderProposal(sessionIdRef);

const applying = computed(() => apply.isPending.value);
const scrollHost = ref<HTMLElement | null>(null);

onMounted(async () => {
  try {
    const created = await create.mutateAsync({
      project_slug: props.projectSlug ?? null,
      workflow_name: props.workflowName ?? null,
      starting_yaml: props.startingYaml ?? null,
    });
    sessionId.value = created.session_id;
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : String(e);
  }
});

watch(
  () => session.data.value?.messages?.length ?? 0,
  () => {
    void nextTick(() => {
      if (scrollHost.value) {
        scrollHost.value.scrollTop = scrollHost.value.scrollHeight;
      }
    });
  },
);

async function onSend(): Promise<void> {
  errorMsg.value = null;
  const text = draft.value.trim();
  if (!text || !sessionId.value) return;
  draft.value = "";
  try {
    await send.mutateAsync({ text });
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : String(e);
  }
}

async function onApply(m: BuilderMessage): Promise<void> {
  if (!m.proposed_yaml || !sessionId.value) return;
  errorMsg.value = null;
  applyingId.value = m.id;
  try {
    const r = await apply.mutateAsync({ proposed_yaml: m.proposed_yaml });
    emit("applied", r.current_yaml);
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : String(e);
  } finally {
    applyingId.value = null;
  }
}

function formatAt(at: string | undefined): string {
  if (!at) return "—";
  try {
    return new Date(at).toLocaleTimeString();
  } catch {
    return at;
  }
}
</script>
