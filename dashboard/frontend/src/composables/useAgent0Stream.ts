import { ref, computed, onUnmounted } from "vue";
import type { ReplaySseEvent, SseEvent } from "@/api/schema.d";

export interface StreamEntry {
  /** Unique key: "{source}:{seq}" */
  key: string;
  seq: number;
  timestamp: string;
  event_type: string;
  source: string;
  stage_id: string | null;
  subagent_id: string | null;
  payload: Record<string, unknown>;
}

export interface StreamFilters {
  hideToolCalls?: boolean;
  hideEngineNudges?: boolean;
  proseOnly?: boolean;
}

const TOOL_EVENT_TYPES = new Set([
  "tool_invoked",
  "tool_result_received",
]);

const ENGINE_NUDGE_EVENT_TYPES = new Set([
  "engine_nudge_emitted",
]);

const PROSE_EVENT_TYPES = new Set([
  "agent0_prose",
  "chat_message_sent_to_session",
  "chat_message_received_from_session",
]);

/** Binary-search insertion index to maintain chronological order by timestamp. */
function bisectByTimestamp(arr: StreamEntry[], ts: string): number {
  let lo = 0;
  let hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if ((arr[mid]?.timestamp ?? "") <= ts) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

/**
 * Subscribes to /sse/stage/{jobSlug}/{stageId} and maintains a sorted,
 * deduplicated transcript of stream events.
 *
 * Merge algorithm per design doc § Agent0 stream pane:
 * - Events sorted by timestamp (binary-search insert, O(log n)).
 * - Deduplication by (source, seq) key.
 * - Out-of-order events always inserted at correct timestamp position.
 * - Auto-scroll-with-anchor state (stickToBottom / newCount).
 */
export function useAgent0Stream(jobSlug: string, stageId: string) {
  const entries = ref<StreamEntry[]>([]);
  const stickToBottom = ref(true);
  const newCount = ref(0);
  const filters = ref<StreamFilters>({});

  const seen = new Set<string>();

  function insertEntry(entry: StreamEntry): void {
    const key = entry.key;
    if (seen.has(key)) return;
    seen.add(key);

    const idx = bisectByTimestamp(entries.value, entry.timestamp);
    entries.value.splice(idx, 0, entry);

    if (!stickToBottom.value) {
      newCount.value += 1;
    }
  }

  function handleEvent(raw: SseEvent): void {
    if (!("seq" in raw)) return; // live CacheChange events — not stream entries
    const e = raw as ReplaySseEvent;
    const entry: StreamEntry = {
      key: `${e.source}:${e.seq}`,
      seq: e.seq,
      timestamp: e.timestamp,
      event_type: e.event_type,
      source: e.source,
      stage_id: e.stage_id,
      subagent_id: e.subagent_id,
      payload: e.payload,
    };
    insertEntry(entry);
  }

  const url = `/sse/stage/${jobSlug}/${stageId}`;
  const source = new EventSource(url);

  source.onmessage = (raw) => {
    try {
      const event = JSON.parse(raw.data as string) as SseEvent;
      handleEvent(event);
    } catch {
      // malformed event — skip
    }
  };

  onUnmounted(() => {
    source.close();
  });

  function setStickToBottom(value: boolean): void {
    stickToBottom.value = value;
  }

  function resetNewCount(): void {
    newCount.value = 0;
    stickToBottom.value = true;
  }

  function setFilters(f: StreamFilters): void {
    filters.value = { ...filters.value, ...f };
  }

  const filteredEntries = computed<StreamEntry[]>(() => {
    const f = filters.value;
    if (!f.hideToolCalls && !f.hideEngineNudges && !f.proseOnly) {
      return entries.value;
    }
    return entries.value.filter((e) => {
      if (f.proseOnly) return PROSE_EVENT_TYPES.has(e.event_type);
      if (f.hideToolCalls && TOOL_EVENT_TYPES.has(e.event_type)) return false;
      if (f.hideEngineNudges && ENGINE_NUDGE_EVENT_TYPES.has(e.event_type)) return false;
      return true;
    });
  });

  return {
    entries,
    filteredEntries,
    stickToBottom,
    newCount,
    setStickToBottom,
    resetNewCount,
    setFilters,
  };
}
