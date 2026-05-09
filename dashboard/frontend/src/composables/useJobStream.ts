import { useQueryClient } from "@tanstack/vue-query";
import { onBeforeUnmount, onMounted, ref, watch, type Ref } from "vue";

import { QUERY_KEYS } from "@/api/queries";

export interface JobStreamHandle {
  /** True while the EventSource is open. */
  connected: Ref<boolean>;
  /** Force a reconnect (e.g. after the slug changes). */
  reconnect: () => void;
}

/**
 * Open a Server-Sent Events stream for a job and route incoming events
 * to vue-query invalidations. Returns a `connected` ref so the caller
 * can show a "live" indicator.
 *
 * Event types — see `dashboard/api/sse.py`:
 * - `ping`                              (no-op)
 * - `node_state_changed`                (slug, node_id) — invalidates job + node + orchestrator events
 * - `chat_appended`                     (slug, node_id) — invalidates node chat
 * - `orchestrator_appended`             (slug)          — invalidates orchestrator chat (Log)
 * - `orchestrator_message_appended`     (slug)          — invalidates orchestrator messages (Chat)
 * - `awaiting_human`                    (slug, node_id) — invalidates node + job + orchestrator events
 * - `human_decision_received`           (slug, node_id) — invalidates node + job + orchestrator events
 * - `job_state_changed`                 (slug)          — invalidates job + jobs list + orchestrator events
 */
export function useJobStream(slug: Ref<string>): JobStreamHandle {
  const qc = useQueryClient();
  const connected = ref(false);
  let es: EventSource | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectAttempts = 0;

  function close(): void {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (es) {
      es.close();
      es = null;
    }
    connected.value = false;
  }

  function scheduleReconnect(): void {
    if (reconnectTimer) return;
    // Exponential backoff capped at 8s. Browsers also do their own
    // EventSource reconnect, but we re-create explicitly so a permanently
    // closed connection (e.g. server restart) doesn't leave us silent.
    const delay = Math.min(1000 * 2 ** reconnectAttempts, 8000);
    reconnectAttempts += 1;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      open();
    }, delay);
  }

  function open(): void {
    close();
    if (!slug.value) return;
    const url = `/sse/jobs/${encodeURIComponent(slug.value)}`;
    es = new EventSource(url);

    es.addEventListener("open", () => {
      connected.value = true;
      reconnectAttempts = 0;
    });
    es.addEventListener("error", () => {
      connected.value = false;
      // EventSource readyState 2 = CLOSED. Browser won't reconnect on its
      // own from CLOSED, so we schedule one. If readyState is CONNECTING
      // (0), the browser is already retrying — leave it.
      if (es && es.readyState === EventSource.CLOSED) {
        scheduleReconnect();
      }
    });
    es.addEventListener("ping", () => {
      connected.value = true;
      reconnectAttempts = 0;
    });

    es.addEventListener("node_state_changed", (ev: MessageEvent) => {
      const { node_id } = parsePayload(ev.data);
      if (!node_id) return;
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.job(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.node(slug.value, node_id) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.orchestratorEvents(slug.value) });
    });

    es.addEventListener("chat_appended", (ev: MessageEvent) => {
      const { node_id } = parsePayload(ev.data);
      if (!node_id) return;
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.chat(slug.value, node_id) });
    });

    es.addEventListener("orchestrator_appended", () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.orchestratorChat(slug.value) });
    });

    es.addEventListener("orchestrator_message_appended", () => {
      // Operator's chat with the orchestrator. Refetch immediately —
      // this is the primary feedback channel for the 2-way HIL.
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.orchestratorMessages(slug.value) });
    });

    es.addEventListener("awaiting_human", (ev: MessageEvent) => {
      const { node_id } = parsePayload(ev.data);
      if (!node_id) return;
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.job(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.node(slug.value, node_id) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.orchestratorEvents(slug.value) });
    });

    es.addEventListener("human_decision_received", (ev: MessageEvent) => {
      const { node_id } = parsePayload(ev.data);
      if (!node_id) return;
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.job(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.node(slug.value, node_id) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.orchestratorEvents(slug.value) });
    });

    es.addEventListener("job_state_changed", () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.job(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.jobs() });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.orchestratorEvents(slug.value) });
    });
  }

  function parsePayload(data: string): { slug?: string; node_id?: string } {
    try {
      const obj = JSON.parse(data);
      if (typeof obj === "object" && obj) return obj;
    } catch {
      /* ignore malformed payloads */
    }
    return {};
  }

  onMounted(open);
  onBeforeUnmount(close);
  watch(slug, open);

  return {
    connected,
    reconnect: open,
  };
}
