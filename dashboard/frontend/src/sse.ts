import { onUnmounted, ref } from "vue";
import type { ReplaySseEvent, SseEvent, SseScope } from "@/api/schema.d";

export interface UseEventStreamOptions {
  onEvent?: (event: SseEvent) => void;
  onConnect?: () => void;
  onDisconnect?: () => void;
}

export interface UseEventStreamReturn {
  connected: ReturnType<typeof ref<boolean>>;
  lastSeq: ReturnType<typeof ref<number | null>>;
  error: ReturnType<typeof ref<string | null>>;
  close: () => void;
}

/**
 * Subscribe to a v1 SSE scope (`global`, `job/<slug>`, or
 * `node/<slug>/<node_id>`). EventSource handles reconnection per spec;
 * this wrapper exposes `connected` / `lastSeq` / `error` for the caller.
 */
export function useEventStream(
  scope: SseScope,
  options: UseEventStreamOptions = {},
): UseEventStreamReturn {
  const connected = ref(false);
  const lastSeq = ref<number | null>(null);
  const error = ref<string | null>(null);

  let source: EventSource | null = null;

  function open(): void {
    source = new EventSource(`/sse/${scope}`);
    source.onopen = () => {
      connected.value = true;
      error.value = null;
      options.onConnect?.();
    };
    source.onerror = () => {
      connected.value = false;
      error.value = "SSE connection error — browser will retry";
      options.onDisconnect?.();
    };
    source.onmessage = (raw) => {
      try {
        const event = JSON.parse(raw.data as string) as SseEvent;
        if ("seq" in event) {
          lastSeq.value = (event as ReplaySseEvent).seq;
        }
        options.onEvent?.(event);
      } catch {
        // malformed event — skip
      }
    };
  }

  function close(): void {
    source?.close();
    source = null;
    connected.value = false;
  }

  open();
  onUnmounted(close);

  return { connected, lastSeq, error, close };
}
