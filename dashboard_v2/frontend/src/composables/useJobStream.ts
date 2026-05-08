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
 * Event types — see `dashboard_v2/api/sse.py`:
 * - `ping`                       (no-op)
 * - `node_state_changed`         (slug, node_id) — invalidates job + node
 * - `chat_appended`              (slug, node_id) — invalidates node chat
 * - `orchestrator_appended`      (slug)          — invalidates orchestrator chat
 * - `awaiting_human`             (slug, node_id) — invalidates node + job
 * - `human_decision_received`    (slug, node_id) — invalidates node + job
 * - `job_state_changed`          (slug)          — invalidates job + jobs list
 */
export function useJobStream(slug: Ref<string>): JobStreamHandle {
  const qc = useQueryClient();
  const connected = ref(false);
  let es: EventSource | null = null;

  function close(): void {
    if (es) {
      es.close();
      es = null;
    }
    connected.value = false;
  }

  function open(): void {
    close();
    if (!slug.value) return;
    const url = `/sse/jobs/${encodeURIComponent(slug.value)}`;
    es = new EventSource(url);

    es.addEventListener("open", () => {
      connected.value = true;
    });
    es.addEventListener("error", () => {
      connected.value = false;
    });
    es.addEventListener("ping", () => {
      connected.value = true;
    });

    es.addEventListener("node_state_changed", (ev: MessageEvent) => {
      const { node_id } = parsePayload(ev.data);
      if (!node_id) return;
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.job(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.node(slug.value, node_id) });
    });

    es.addEventListener("chat_appended", (ev: MessageEvent) => {
      const { node_id } = parsePayload(ev.data);
      if (!node_id) return;
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.chat(slug.value, node_id) });
    });

    es.addEventListener("orchestrator_appended", () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.orchestratorChat(slug.value) });
    });

    es.addEventListener("awaiting_human", (ev: MessageEvent) => {
      const { node_id } = parsePayload(ev.data);
      if (!node_id) return;
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.job(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.node(slug.value, node_id) });
    });

    es.addEventListener("human_decision_received", (ev: MessageEvent) => {
      const { node_id } = parsePayload(ev.data);
      if (!node_id) return;
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.job(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.node(slug.value, node_id) });
    });

    es.addEventListener("job_state_changed", () => {
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.job(slug.value) });
      void qc.invalidateQueries({ queryKey: QUERY_KEYS.jobs() });
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
