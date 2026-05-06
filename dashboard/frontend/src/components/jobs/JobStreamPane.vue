<template>
  <section class="flex h-full flex-col">
    <header
      class="flex items-center justify-between border-b border-border pb-2 text-xs text-text-secondary"
    >
      <span class="uppercase">Live stream</span>
      <span class="flex items-center gap-1.5">
        <span
          :class="connected ? 'bg-green-500' : 'bg-gray-500'"
          class="inline-block h-1.5 w-1.5 rounded-full"
        />
        {{ connected ? `live · ${events.length} events` : "disconnected" }}
      </span>
    </header>

    <ul ref="scrollEl" class="mt-2 flex-1 overflow-auto font-mono text-xs">
      <li
        v-for="(event, i) in events"
        :key="`${event.seq}-${i}`"
        class="flex gap-2 border-b border-border/30 px-1 py-1"
      >
        <span class="w-20 shrink-0 text-text-secondary">{{ formatTime(event.timestamp) }}</span>
        <span class="w-12 shrink-0 text-text-secondary">{{ event.source }}</span>
        <span :class="eventTypeClass(event.event_type)" class="shrink-0">
          {{ event.event_type }}
        </span>
        <span class="text-text-secondary">{{ summarisePayload(event) }}</span>
      </li>
      <li v-if="events.length === 0" class="px-1 py-2 text-text-secondary">Waiting for events…</li>
    </ul>
  </section>
</template>

<script setup lang="ts">
import { nextTick, ref, watch } from "vue";
import { useEventStream } from "@/sse";
import type { ReplaySseEvent, SseEvent } from "@/api/schema.d";

const props = defineProps<{
  jobSlug: string;
}>();

const events = ref<ReplaySseEvent[]>([]);
const scrollEl = ref<HTMLUListElement | null>(null);

const { connected } = useEventStream(`job/${props.jobSlug}`, {
  onEvent: (event: SseEvent) => {
    if (!("seq" in event)) return; // ignore live PathChange events here
    events.value.push(event as ReplaySseEvent);
    void nextTick(() => {
      const el = scrollEl.value;
      if (el) el.scrollTop = el.scrollHeight;
    });
  },
});

watch(
  () => props.jobSlug,
  () => {
    // Job changed — clear the buffer so we don't intermix events from
    // a previous job. The new EventSource is created via re-mount.
    events.value = [];
  },
);

function formatTime(iso: string): string {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

function eventTypeClass(type: string): string {
  if (type.endsWith("_failed") || type === "node_failed") return "text-red-300";
  if (type.endsWith("_succeeded") || type.endsWith("_completed")) return "text-green-300";
  if (type.endsWith("_started")) return "text-blue-300";
  if (type.startsWith("hil_")) return "text-amber-300";
  return "text-text-primary";
}

function summarisePayload(event: ReplaySseEvent): string {
  const parts: string[] = [];
  if (event.stage_id) parts.push(event.stage_id);
  const payload = event.payload || {};
  for (const [k, v] of Object.entries(payload)) {
    if (v === null || v === undefined) continue;
    if (k === "iter" && Array.isArray(v) && v.length === 0) continue;
    if (typeof v === "string" && v.length > 80) {
      parts.push(`${k}=${v.slice(0, 77)}…`);
    } else if (typeof v === "object") {
      parts.push(`${k}=${JSON.stringify(v).slice(0, 80)}`);
    } else {
      parts.push(`${k}=${v}`);
    }
  }
  return parts.join(" ");
}
</script>
