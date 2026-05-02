import { describe, it, expect, beforeEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useGlobalStore } from "@/stores/global";
import type { SseEvent } from "@/api/schema.d";

function makeEvent(overrides: Partial<SseEvent> = {}): SseEvent {
  return {
    seq: 1,
    timestamp: "2026-05-02T10:00:00Z",
    event_type: "stage_state_transition",
    source: "job_driver",
    job_id: "test-job",
    stage_id: null,
    task_id: null,
    subagent_id: null,
    parent_event_seq: null,
    payload: {},
    ...overrides,
  };
}

describe("useGlobalStore", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("starts with zero HIL awaiting count", () => {
    const store = useGlobalStore();
    expect(store.hilAwaitingCount).toBe(0);
  });

  it("increments HIL count on hil_opened event", () => {
    const store = useGlobalStore();
    store.applyEvent(makeEvent({ event_type: "hil_opened", seq: 1 }));
    expect(store.hilAwaitingCount).toBe(1);
    store.applyEvent(makeEvent({ event_type: "hil_opened", seq: 2 }));
    expect(store.hilAwaitingCount).toBe(2);
  });

  it("decrements HIL count on hil_answered event", () => {
    const store = useGlobalStore();
    store.applyEvent(makeEvent({ event_type: "hil_opened", seq: 1 }));
    store.applyEvent(makeEvent({ event_type: "hil_answered", seq: 2 }));
    expect(store.hilAwaitingCount).toBe(0);
  });

  it("decrements HIL count on hil_cancelled event", () => {
    const store = useGlobalStore();
    store.applyEvent(makeEvent({ event_type: "hil_opened", seq: 1 }));
    store.applyEvent(makeEvent({ event_type: "hil_cancelled", seq: 2 }));
    expect(store.hilAwaitingCount).toBe(0);
  });

  it("never goes below 0 on decrement", () => {
    const store = useGlobalStore();
    store.applyEvent(makeEvent({ event_type: "hil_answered", seq: 1 }));
    expect(store.hilAwaitingCount).toBe(0);
  });

  it("tracks lastEventSeq", () => {
    const store = useGlobalStore();
    store.applyEvent(makeEvent({ seq: 42 }));
    expect(store.lastEventSeq).toBe(42);
  });

  it("updates connected flag via setConnected", () => {
    const store = useGlobalStore();
    expect(store.connected).toBe(false);
    store.setConnected(true);
    expect(store.connected).toBe(true);
    store.setConnected(false);
    expect(store.connected).toBe(false);
  });
});
