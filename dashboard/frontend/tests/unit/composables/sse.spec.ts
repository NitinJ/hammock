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

  // Test helpers
  triggerOpen(): void {
    this.onopen?.(new Event("open"));
  }

  triggerError(): void {
    this.onerror?.(new Event("error"));
  }

  triggerMessage(data: unknown): void {
    const ev = new MessageEvent("message", { data: JSON.stringify(data) });
    this.onmessage?.(ev);
  }
}

vi.stubGlobal("EventSource", MockEventSource);

import { useEventStream } from "@/sse";
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

describe("useEventStream", () => {
  beforeEach(() => {
    MockEventSource.instances.length = 0;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("creates an EventSource for the correct URL", () => {
    const [, unmount] = withSetup(() => useEventStream("global"));
    expect(MockEventSource.instances).toHaveLength(1);
    expect(MockEventSource.instances[0]?.url).toBe("/sse/global");
    unmount();
  });

  it("sets connected to true on open", () => {
    const [stream, unmount] = withSetup(() => useEventStream("global"));
    expect(stream.connected.value).toBe(false);
    MockEventSource.instances[0]?.triggerOpen();
    expect(stream.connected.value).toBe(true);
    unmount();
  });

  it("sets connected to false and error on SSE error", () => {
    const [stream, unmount] = withSetup(() => useEventStream("global"));
    MockEventSource.instances[0]?.triggerOpen();
    MockEventSource.instances[0]?.triggerError();
    expect(stream.connected.value).toBe(false);
    expect(stream.error.value).toBeTruthy();
    unmount();
  });

  it("calls onEvent with parsed event payload", () => {
    const onEvent = vi.fn();
    const [, unmount] = withSetup(() => useEventStream("global", { onEvent }));
    const event = {
      seq: 5,
      timestamp: "2026-05-02T10:00:00Z",
      event_type: "hil_opened",
      source: "job_driver",
      job_id: "job-1",
      stage_id: null,
      task_id: null,
      subagent_id: null,
      parent_event_seq: null,
      payload: {},
    };
    MockEventSource.instances[0]?.triggerMessage(event);
    expect(onEvent).toHaveBeenCalledWith(event);
    unmount();
  });

  it("tracks lastSeq from events", () => {
    const [stream, unmount] = withSetup(() => useEventStream("global"));
    MockEventSource.instances[0]?.triggerMessage({
      seq: 99,
      timestamp: "",
      event_type: "ping",
      source: "dashboard",
      job_id: "",
      stage_id: null,
      task_id: null,
      subagent_id: null,
      parent_event_seq: null,
      payload: {},
    });
    expect(stream.lastSeq.value).toBe(99);
    unmount();
  });

  it("closes EventSource on unmount", () => {
    const [, unmount] = withSetup(() => useEventStream("global"));
    const src = MockEventSource.instances[0]!;
    expect(src.closed).toBe(false);
    unmount();
    expect(src.closed).toBe(true);
  });

  it("close() method closes the source", () => {
    const [stream, unmount] = withSetup(() => useEventStream("global"));
    const src = MockEventSource.instances[0]!;
    stream.close();
    expect(src.closed).toBe(true);
    unmount();
  });

  it("builds correct URL for job scope", () => {
    const [, unmount] = withSetup(() => useEventStream("job/my-job-slug"));
    expect(MockEventSource.instances[0]?.url).toBe("/sse/job/my-job-slug");
    unmount();
  });

  it("builds correct URL for stage scope", () => {
    const [, unmount] = withSetup(() => useEventStream("stage/my-job/stage-1"));
    expect(MockEventSource.instances[0]?.url).toBe("/sse/stage/my-job/stage-1");
    unmount();
  });
});
