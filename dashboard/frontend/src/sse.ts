import { ref, onUnmounted } from "vue";
import type { ReplaySseEvent, SseEvent } from "@/api/schema.d";

export type SseScope = "global" | `job/${string}` | `stage/${string}/${string}`;

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
 * Subscribes to a scoped SSE endpoint with Last-Event-ID replay on reconnect.
 * Wraps native EventSource; reconnection is built-in to the browser spec.
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
    const url = `/sse/${scope}`;
    source = new EventSource(url);

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
        // Replay events carry seq; live CacheChange events do not.
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
