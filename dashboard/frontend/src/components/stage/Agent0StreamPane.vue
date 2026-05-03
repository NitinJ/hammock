<template>
  <div class="flex flex-col h-full">
    <!-- Filter bar -->
    <div class="px-2 py-1 border-b border-border">
      <StreamFilters v-model="filters" />
    </div>

    <!-- Anchor button when not at bottom -->
    <div v-if="!stream.stickToBottom.value && stream.newCount.value > 0" class="px-2 py-1 text-center">
      <button
        class="text-xs bg-primary text-white px-2 py-0.5 rounded"
        @click="scrollToBottom"
      >
        ↓ Live ({{ stream.newCount.value }} new)
      </button>
    </div>

    <!-- Stream entries -->
    <div
      ref="scrollEl"
      class="flex-1 overflow-y-auto px-2 py-1 space-y-0.5 min-h-0"
      @scroll="onScroll"
    >
      <template v-for="entry in stream.filteredEntries.value" :key="entry.key">
        <ProseMessage
          v-if="entry.event_type === 'agent0_prose'"
          :text="(entry.payload['text'] as string) || ''"
          :timestamp="entry.timestamp"
        />
        <ToolCall
          v-else-if="entry.event_type === 'tool_invoked' || entry.event_type === 'tool_result_received'"
          :tool-name="(entry.payload['tool_name'] as string) || entry.event_type"
          :result="(entry.payload['result'] as string) || (entry.payload['input'] as string) || ''"
          :duration-ms="(entry.payload['duration_ms'] as number) || 0"
          :timestamp="entry.timestamp"
        />
        <EngineNudge
          v-else-if="entry.event_type === 'engine_nudge_emitted'"
          :text="(entry.payload['text'] as string) || ''"
          :timestamp="entry.timestamp"
        />
        <HumanChat
          v-else-if="entry.event_type === 'chat_message_sent_to_session'"
          :text="(entry.payload['text'] as string) || ''"
          :timestamp="entry.timestamp"
        />
        <AgentReply
          v-else-if="entry.event_type === 'chat_message_received_from_session'"
          :text="(entry.payload['text'] as string) || ''"
          :timestamp="entry.timestamp"
        />
        <SubAgentRegion
          v-else-if="entry.event_type === 'subagent_dispatched'"
          :subagent-id="(entry.payload['subagent_id'] as string) || entry.subagent_id || 'subagent'"
          :message-count="(entry.payload['message_count'] as number) || 0"
          :tool-call-count="(entry.payload['tool_call_count'] as number) || 0"
          :cost-usd="(entry.payload['cost_usd'] as number) || 0"
          :state="((entry.payload['state'] as StageState | TaskState | undefined) ?? 'RUNNING') as StageState"
        />
        <!-- Fallback: unknown event type — skip -->
      </template>

      <!-- Empty state -->
      <p v-if="stream.filteredEntries.value.length === 0" class="text-text-secondary text-sm italic py-4 text-center">
        Waiting for events…
      </p>
    </div>

    <!-- Chat input -->
    <ChatInput
      class="px-2 py-2"
      :job-slug="jobSlug"
      :stage-id="stageId"
      @send="sendChat"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from "vue";
import { useAgent0Stream } from "@/composables/useAgent0Stream";
import type { StreamFilters as Filters } from "@/composables/useAgent0Stream";
import type { StageState, TaskState } from "@/api/schema.d";
import ProseMessage from "./ProseMessage.vue";
import ToolCall from "./ToolCall.vue";
import EngineNudge from "./EngineNudge.vue";
import HumanChat from "./HumanChat.vue";
import AgentReply from "./AgentReply.vue";
import SubAgentRegion from "./SubAgentRegion.vue";
import ChatInput from "./ChatInput.vue";
import StreamFilters from "./StreamFilters.vue";

const props = defineProps<{ jobSlug: string; stageId: string }>();

const stream = useAgent0Stream(props.jobSlug, props.stageId);
const filters = ref<Filters>({});
const scrollEl = ref<HTMLElement | null>(null);

watch(filters, (f) => stream.setFilters(f), { deep: true });

watch(stream.entries, async () => {
  if (!stream.stickToBottom.value) return;
  await nextTick();
  scrollEl.value?.scrollTo({ top: scrollEl.value.scrollHeight });
});

function onScroll(): void {
  const el = scrollEl.value;
  if (!el) return;
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 8;
  stream.setStickToBottom(atBottom);
}

function scrollToBottom(): void {
  stream.resetNewCount();
  nextTick(() => {
    scrollEl.value?.scrollTo({ top: scrollEl.value.scrollHeight, behavior: "smooth" });
  });
}

async function sendChat(text: string): Promise<void> {
  await fetch(`/api/jobs/${props.jobSlug}/stages/${props.stageId}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
}
</script>
