<template>
  <section class="surface p-5 space-y-4">
    <header>
      <h2 class="font-mono text-base text-text-primary">orchestrator</h2>
      <p class="text-xs text-text-tertiary">
        The master claude agent driving this job. Spawns one subagent per node, validates outputs,
        handles HIL.
      </p>
    </header>

    <div class="flex items-center gap-1 border-b border-border">
      <button
        v-for="t in tabs"
        :key="t.id"
        type="button"
        :class="[
          'px-3 py-2 text-xs uppercase tracking-wider transition-colors',
          tab === t.id
            ? 'text-text-primary border-b-2 border-accent'
            : 'text-text-tertiary hover:text-text-secondary',
        ]"
        @click="tab = t.id"
      >
        {{ t.label }}
      </button>
    </div>

    <div v-if="tab === 'events'">
      <ul v-if="(events.data.value?.events ?? []).length > 0" class="space-y-1.5">
        <li
          v-for="(ev, i) in events.data.value?.events ?? []"
          :key="`${ev.kind}-${i}`"
          class="flex items-start gap-3 text-xs font-mono"
        >
          <span class="w-44 shrink-0 text-text-tertiary">{{ formatAt(ev.at) }}</span>
          <span :class="['w-32 shrink-0', kindColor(ev.kind)]">{{ ev.kind }}</span>
          <span class="flex-1 text-text-secondary">{{ ev.detail }}</span>
        </li>
      </ul>
      <p v-else class="text-text-tertiary text-sm">No events yet.</p>
    </div>

    <ChatTail v-else-if="tab === 'chat'" :chat="chat.data.value" />

    <section v-else-if="tab === 'messages'" class="space-y-3">
      <div
        v-if="(messages.data.value?.messages ?? []).length === 0"
        class="text-text-tertiary text-sm"
      >
        No messages yet. Start the conversation below.
      </div>
      <ul v-else class="space-y-2 max-h-[55vh] overflow-auto">
        <li
          v-for="(m, i) in messages.data.value?.messages ?? []"
          :key="m.id ?? i"
          :class="['flex', m.from === 'operator' ? 'justify-end' : 'justify-start']"
        >
          <div
            :class="[
              'max-w-[80%] surface p-3 text-sm',
              m.from === 'operator' ? 'bg-accent/10 border-accent/40' : '',
            ]"
          >
            <div class="text-[10px] uppercase tracking-wider text-text-tertiary mb-1">
              {{ m.from }} · {{ formatAt(m.timestamp) }}
            </div>
            <MarkdownView :source="m.text" />
          </div>
        </li>
      </ul>
      <form class="flex items-end gap-2" @submit.prevent="onSendMessage">
        <textarea
          v-model="messageDraft"
          rows="2"
          placeholder="Chat with the orchestrator (e.g., 'skip implement', 'rerun X with this comment', 'pause')"
          class="input font-mono text-xs flex-1"
        ></textarea>
        <button
          type="submit"
          class="btn-accent text-sm shrink-0"
          :disabled="!messageDraft.trim() || send.isPending.value"
        >
          {{ send.isPending.value ? "Sending…" : "Send" }}
        </button>
      </form>
    </section>
  </section>
</template>

<script setup lang="ts">
import { ref, toRef } from "vue";

import ChatTail from "@/components/ChatTail.vue";
import MarkdownView from "@/components/MarkdownView.vue";
import {
  useOrchestratorChat,
  useOrchestratorEvents,
  useOrchestratorMessages,
  useSendOrchestratorMessage,
} from "@/api/queries";

const props = defineProps<{ slug: string }>();
const slugRef = toRef(props, "slug");
const chat = useOrchestratorChat(slugRef);
const events = useOrchestratorEvents(slugRef);
const messages = useOrchestratorMessages(slugRef);
const send = useSendOrchestratorMessage(slugRef);

const tabs = [
  { id: "events", label: "Events" },
  { id: "messages", label: "Chat" },
  { id: "chat", label: "Log" },
];

const tab = ref<string>("events");
const messageDraft = ref("");

async function onSendMessage(): Promise<void> {
  const text = messageDraft.value.trim();
  if (!text) return;
  try {
    await send.mutateAsync({ text });
    messageDraft.value = "";
  } catch (e) {
    // surface via mutation state in future; for now console
    console.error(e);
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

function kindColor(kind: string): string {
  if (kind.startsWith("job_completed")) return "text-state-succeeded";
  if (kind.startsWith("job_failed")) return "text-state-failed";
  if (kind.startsWith("node_succeeded")) return "text-state-succeeded";
  if (kind.startsWith("node_failed")) return "text-state-failed";
  if (kind.startsWith("node_running")) return "text-state-running";
  if (kind === "awaiting_human") return "text-state-awaiting";
  if (kind === "human_decision") return "text-accent";
  if (kind === "validation_failure") return "text-state-failed";
  return "text-text-tertiary";
}
</script>
