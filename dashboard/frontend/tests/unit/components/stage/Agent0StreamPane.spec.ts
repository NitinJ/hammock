import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

// Mock EventSource before importing the component
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

  triggerMessage(data: unknown): void {
    const ev = new MessageEvent("message", { data: JSON.stringify(data) });
    this.onmessage?.(ev);
  }
}

vi.stubGlobal("EventSource", MockEventSource);
vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve({}) }));

import Agent0StreamPane from "@/components/stage/Agent0StreamPane.vue";

describe("Agent0StreamPane", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    MockEventSource.instances.length = 0;
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("renders and opens SSE for the correct stage", () => {
    mount(Agent0StreamPane, {
      props: { jobSlug: "my-job", stageId: "implement" },
    });
    expect(MockEventSource.instances[0]?.url).toBe("/sse/stage/my-job/implement");
  });

  it("renders prose messages in the stream", async () => {
    const wrapper = mount(Agent0StreamPane, {
      props: { jobSlug: "my-job", stageId: "implement" },
    });
    const src = MockEventSource.instances[0]!;
    src.triggerMessage({
      seq: 1,
      timestamp: "2026-05-01T12:00:01.000Z",
      event_type: "agent0_prose",
      source: "agent0",
      job_id: "my-job",
      stage_id: "implement",
      task_id: null,
      subagent_id: null,
      parent_event_seq: null,
      payload: { text: "Starting implementation now." },
    });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("Starting implementation now.");
  });

  it("renders tool call events", async () => {
    const wrapper = mount(Agent0StreamPane, {
      props: { jobSlug: "my-job", stageId: "implement" },
    });
    const src = MockEventSource.instances[0]!;
    src.triggerMessage({
      seq: 1,
      timestamp: "2026-05-01T12:00:01.000Z",
      event_type: "tool_invoked",
      source: "agent0",
      job_id: "my-job",
      stage_id: "implement",
      task_id: null,
      subagent_id: null,
      parent_event_seq: null,
      payload: { tool_name: "Bash", input: "ls -la" },
    });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("Bash");
  });

  it("renders chat input at the bottom", () => {
    const wrapper = mount(Agent0StreamPane, {
      props: { jobSlug: "my-job", stageId: "implement" },
    });
    expect(wrapper.find("textarea").exists()).toBe(true);
  });

  it("renders filters UI", () => {
    const wrapper = mount(Agent0StreamPane, {
      props: { jobSlug: "my-job", stageId: "implement" },
    });
    const text = wrapper.text().toLowerCase();
    expect(text).toMatch(/tool|filter|nudge|prose/);
  });
});
