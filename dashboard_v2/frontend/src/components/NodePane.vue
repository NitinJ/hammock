<template>
  <div class="surface p-5 space-y-4">
    <header class="flex items-center justify-between gap-3">
      <div>
        <h2 class="text-sm font-mono text-text-primary">{{ nodeId }}</h2>
        <p class="text-xs text-text-tertiary">
          <span v-if="node.data.value">{{ node.data.value.state }}</span>
        </p>
      </div>
      <StatePill
        v-if="node.data.value"
        :state="effectiveState(node.data.value.state, node.data.value.awaiting_human)"
      />
    </header>

    <div v-if="node.isPending.value" class="text-text-tertiary text-sm">Loading…</div>
    <div v-else-if="node.isError.value" class="text-state-failed text-sm">
      Node not found yet — orchestrator may still be initialising.
    </div>
    <template v-else-if="node.data.value">
      <HumanReviewPanel v-if="node.data.value.awaiting_human" :slug="slug" :node-id="nodeId" />

      <nav class="flex gap-1 border-b border-border">
        <button
          v-for="t in tabs"
          :key="t"
          type="button"
          :class="[
            'px-3 py-2 text-xs uppercase tracking-wider border-b-2 transition-colors',
            tab === t
              ? 'border-accent text-text-primary'
              : 'border-transparent text-text-tertiary hover:text-text-secondary',
          ]"
          @click="tab = t"
        >
          {{ t }}
        </button>
      </nav>

      <section v-if="tab === 'output'" class="min-h-[20vh]">
        <div v-if="!node.data.value.output" class="text-text-tertiary text-sm">No output yet.</div>
        <MarkdownView v-else :source="node.data.value.output" />
      </section>

      <section v-else-if="tab === 'prompt'" class="min-h-[20vh]">
        <pre class="font-mono text-xs whitespace-pre-wrap text-text-secondary">{{
          node.data.value.prompt || "(no prompt yet)"
        }}</pre>
      </section>

      <section v-else-if="tab === 'input'" class="min-h-[20vh]">
        <MarkdownView :source="node.data.value.input || '*(no input yet)*'" />
      </section>

      <section v-else-if="tab === 'chat'" class="min-h-[20vh]">
        <ChatTail :chat="chat.data.value" />
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, toRef } from "vue";

import StatePill from "@/components/StatePill.vue";
import MarkdownView from "@/components/MarkdownView.vue";
import ChatTail from "@/components/ChatTail.vue";
import HumanReviewPanel from "@/components/HumanReviewPanel.vue";
import { useNode, useNodeChat } from "@/api/queries";

const props = defineProps<{ slug: string; nodeId: string }>();
const slugRef = computed(() => props.slug);
const nodeIdRef = computed<string | null>(() => props.nodeId);

const node = useNode(slugRef, nodeIdRef);
const chat = useNodeChat(slugRef, nodeIdRef);

const tabs = ["output", "chat", "prompt", "input"] as const;
const tab = ref<(typeof tabs)[number]>("output");

type PillState =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "awaiting"
  | "submitted"
  | "completed"
  | "blocked_on_human";

function effectiveState(state: string, awaiting: boolean): PillState {
  if (awaiting) return "awaiting";
  if (
    state === "pending" ||
    state === "running" ||
    state === "succeeded" ||
    state === "failed" ||
    state === "submitted" ||
    state === "completed" ||
    state === "blocked_on_human"
  ) {
    return state;
  }
  return "pending";
}

// keep linters happy on unused refs
const _unused = toRef(props, "slug");
void _unused.value;
</script>
