import { defineStore } from "pinia";
import { ref } from "vue";
import type { SseEvent } from "@/api/schema.d";

export const useGlobalStore = defineStore("global", () => {
  const hilAwaitingCount = ref(0);
  const lastEventSeq = ref<number | null>(null);
  const connected = ref(false);

  function applyEvent(event: SseEvent): void {
    // F1: SseEvent is a union; LiveSseEvents have no seq — ignore them here.
    if (!("seq" in event)) return;
    lastEventSeq.value = event.seq;
    if (event.event_type === "hil_opened") {
      hilAwaitingCount.value += 1;
    } else if (event.event_type === "hil_answered" || event.event_type === "hil_cancelled") {
      hilAwaitingCount.value = Math.max(0, hilAwaitingCount.value - 1);
    }
  }

  function setConnected(value: boolean): void {
    connected.value = value;
  }

  return { hilAwaitingCount, lastEventSeq, connected, applyEvent, setConnected };
});
