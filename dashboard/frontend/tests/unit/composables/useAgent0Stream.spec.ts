import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mock EventSource before importing the composable
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  triggerOpen(): void {
    this.onopen?.(new Event("open"));
  }

  triggerMessage(data: unknown): void {
    const ev = new MessageEvent("message", { data: JSON.stringify(data) });
    this.onmessage?.(ev);
  }
}

vi.stubGlobal("EventSource", MockEventSource);
vi.stubGlobal("fetch", vi.fn());

import { useAgent0Stream } from "@/composables/useAgent0Stream";
import type { StreamEntry, StreamFilters } from "@/composables/useAgent0Stream";
import { createApp } from "vue";
import { createPinia } from "pinia";

function withSetup<T>(composable: () => T): [T, () => void] {
  let result!: T;
  const app = createApp({
    setup() {
      result = composable();
      return () => null;
    },
  });
  app.use(createPinia());
  app.mount(document.createElement("div"));
  return [result, () => app.unmount()];
}

function makeReplayEvent(overrides: Partial<{
  seq: number;
  timestamp: string;
  event_type: string;
  source: string;
  job_id: string;
  stage_id: string | null;
  subagent_id: string | null;
  payload: Record<string, unknown>;
}> = {}): object {
  return {
    seq: 1,
    timestamp: "2026-05-01T12:00:00.000Z",
    event_type: "agent0_prose",
    source: "agent0",
    job_id: "job-1",
    stage_id: "implement",
    task_id: null,
    subagent_id: null,
    parent_event_seq: null,
    payload: { text: "Hello" },
    ...overrides,
  };
}

describe("useAgent0Stream — merge algorithm", () => {
  beforeEach(() => {
    MockEventSource.instances.length = 0;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("opens SSE for the correct stage scope", () => {
    const [, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    expect(MockEventSource.instances[0]?.url).toBe("/sse/stage/my-job/implement");
    unmount();
  });

  it("starts with empty entries array", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    expect(stream.entries.value).toHaveLength(0);
    unmount();
  });

  it("inserts an event into entries on SSE message", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    const src = MockEventSource.instances[0]!;
    src.triggerMessage(makeReplayEvent({ seq: 1, event_type: "agent0_prose" }));
    expect(stream.entries.value).toHaveLength(1);
    unmount();
  });

  it("orders entries chronologically by timestamp when within 500ms window", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    const src = MockEventSource.instances[0]!;
    // Send later timestamp first, then earlier — within 500ms window so bisect-insert applies
    src.triggerMessage(
      makeReplayEvent({ seq: 2, timestamp: "2026-05-01T12:00:01.400Z" }),
    );
    src.triggerMessage(
      makeReplayEvent({ seq: 1, timestamp: "2026-05-01T12:00:01.000Z" }),
    );
    const times = stream.entries.value.map((e: StreamEntry) => e.timestamp);
    expect(times[0]).toBe("2026-05-01T12:00:01.000Z");
    expect(times[1]).toBe("2026-05-01T12:00:01.400Z");
    unmount();
  });

  it("handles out-of-order events within 500ms window — inserts at correct position", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    const src = MockEventSource.instances[0]!;
    // Establish a 'latest' at t=5s
    src.triggerMessage(
      makeReplayEvent({ seq: 5, timestamp: "2026-05-01T12:00:05.000Z" }),
    );
    // t=4.6s — 400ms behind latest, within window
    src.triggerMessage(
      makeReplayEvent({ seq: 4, timestamp: "2026-05-01T12:00:04.600Z" }),
    );
    const times = stream.entries.value.map((e: StreamEntry) => e.timestamp);
    expect(times[0]).toBe("2026-05-01T12:00:04.600Z");
    expect(times[1]).toBe("2026-05-01T12:00:05.000Z");
    unmount();
  });

  it("handles out-of-order events outside 500ms window — appended at tail, not reordered", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    const src = MockEventSource.instances[0]!;
    src.triggerMessage(
      makeReplayEvent({ seq: 10, timestamp: "2026-05-01T12:00:10.000Z" }),
    );
    // t=9s — 1000ms behind latest, outside 500ms tolerance window
    src.triggerMessage(
      makeReplayEvent({ seq: 9, timestamp: "2026-05-01T12:00:09.000Z" }),
    );
    const times = stream.entries.value.map((e: StreamEntry) => e.timestamp);
    // Late event appended at tail rather than inserted at correct position
    // to avoid visual churn on already-rendered rows.
    expect(times[0]).toBe("2026-05-01T12:00:10.000Z");
    expect(times[1]).toBe("2026-05-01T12:00:09.000Z");
    unmount();
  });

  it("deduplicates events with same (source, seq) — idempotency", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    const src = MockEventSource.instances[0]!;
    const evt = makeReplayEvent({ seq: 1, source: "agent0" });
    src.triggerMessage(evt);
    src.triggerMessage(evt);
    expect(stream.entries.value).toHaveLength(1);
    unmount();
  });

  it("does not deduplicate events with same seq but different source", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    const src = MockEventSource.instances[0]!;
    src.triggerMessage(makeReplayEvent({ seq: 1, source: "agent0" }));
    src.triggerMessage(makeReplayEvent({ seq: 1, source: "human" }));
    expect(stream.entries.value).toHaveLength(2);
    unmount();
  });

  it("stickToBottom starts true (at-bottom state)", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    expect(stream.stickToBottom.value).toBe(true);
    unmount();
  });

  it("setStickToBottom(false) disables auto-scroll", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    stream.setStickToBottom(false);
    expect(stream.stickToBottom.value).toBe(false);
    unmount();
  });

  it("newCount increments when stickToBottom is false and new events arrive", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    stream.setStickToBottom(false);
    const src = MockEventSource.instances[0]!;
    src.triggerMessage(makeReplayEvent({ seq: 1 }));
    src.triggerMessage(makeReplayEvent({ seq: 2, timestamp: "2026-05-01T12:00:02.000Z" }));
    expect(stream.newCount.value).toBe(2);
    unmount();
  });

  it("resetNewCount zeros the counter and re-enables stickToBottom", () => {
    const [stream, unmount] = withSetup(() =>
      useAgent0Stream("my-job", "implement"),
    );
    stream.setStickToBottom(false);
    const src = MockEventSource.instances[0]!;
    src.triggerMessage(makeReplayEvent({ seq: 1 }));
    stream.resetNewCount();
    expect(stream.newCount.value).toBe(0);
    expect(stream.stickToBottom.value).toBe(true);
    unmount();
  });

  describe("filter logic", () => {
    it("filteredEntries returns all entries when no filters active", () => {
      const [stream, unmount] = withSetup(() =>
        useAgent0Stream("my-job", "implement"),
      );
      const src = MockEventSource.instances[0]!;
      src.triggerMessage(makeReplayEvent({ seq: 1, event_type: "agent0_prose", source: "agent0" }));
      src.triggerMessage(makeReplayEvent({ seq: 2, event_type: "tool_invoked", source: "agent0", timestamp: "2026-05-01T12:00:02.000Z" }));
      expect(stream.filteredEntries.value).toHaveLength(2);
      unmount();
    });

    it("hideToolCalls filter removes tool events", () => {
      const [stream, unmount] = withSetup(() =>
        useAgent0Stream("my-job", "implement"),
      );
      const src = MockEventSource.instances[0]!;
      src.triggerMessage(makeReplayEvent({ seq: 1, event_type: "agent0_prose", source: "agent0" }));
      src.triggerMessage(makeReplayEvent({ seq: 2, event_type: "tool_invoked", source: "agent0", timestamp: "2026-05-01T12:00:02.000Z" }));
      stream.setFilters({ hideToolCalls: true });
      expect(stream.filteredEntries.value).toHaveLength(1);
      expect(stream.filteredEntries.value[0]?.event_type).toBe("agent0_prose");
      unmount();
    });

    it("hideEngineNudges filter removes engine nudge events", () => {
      const [stream, unmount] = withSetup(() =>
        useAgent0Stream("my-job", "implement"),
      );
      const src = MockEventSource.instances[0]!;
      src.triggerMessage(makeReplayEvent({ seq: 1, event_type: "agent0_prose", source: "agent0" }));
      src.triggerMessage(makeReplayEvent({ seq: 2, event_type: "engine_nudge_emitted", source: "engine", timestamp: "2026-05-01T12:00:02.000Z" }));
      stream.setFilters({ hideEngineNudges: true });
      expect(stream.filteredEntries.value).toHaveLength(1);
      unmount();
    });

    it("proseOnly keeps only prose and human chat events", () => {
      const [stream, unmount] = withSetup(() =>
        useAgent0Stream("my-job", "implement"),
      );
      const src = MockEventSource.instances[0]!;
      src.triggerMessage(makeReplayEvent({ seq: 1, event_type: "agent0_prose", source: "agent0" }));
      src.triggerMessage(makeReplayEvent({ seq: 2, event_type: "tool_invoked", source: "agent0", timestamp: "2026-05-01T12:00:02.000Z" }));
      src.triggerMessage(makeReplayEvent({ seq: 3, event_type: "chat_message_sent_to_session", source: "human", timestamp: "2026-05-01T12:00:03.000Z" }));
      src.triggerMessage(makeReplayEvent({ seq: 4, event_type: "engine_nudge_emitted", source: "engine", timestamp: "2026-05-01T12:00:04.000Z" }));
      stream.setFilters({ proseOnly: true });
      const types = stream.filteredEntries.value.map((e: StreamEntry) => e.event_type);
      expect(types).toContain("agent0_prose");
      expect(types).toContain("chat_message_sent_to_session");
      expect(types).not.toContain("tool_invoked");
      expect(types).not.toContain("engine_nudge_emitted");
      unmount();
    });
  });
});
