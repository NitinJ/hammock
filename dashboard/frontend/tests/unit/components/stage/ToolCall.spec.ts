import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import ToolCall from "@/components/stage/ToolCall.vue";

describe("ToolCall", () => {
  beforeEach(() => { setActivePinia(createPinia()); });

  it("renders tool name", () => {
    const wrapper = mount(ToolCall, {
      props: {
        toolName: "Bash",
        result: "exit 0",
        durationMs: 1200,
        timestamp: "2026-05-01T12:00:00.000Z",
      },
    });
    expect(wrapper.text()).toContain("Bash");
  });

  it("renders result summary", () => {
    const wrapper = mount(ToolCall, {
      props: {
        toolName: "Read",
        result: "file contents here",
        durationMs: 50,
        timestamp: "2026-05-01T12:00:00.000Z",
      },
    });
    expect(wrapper.text()).toContain("file contents here");
  });

  it("renders duration", () => {
    const wrapper = mount(ToolCall, {
      props: {
        toolName: "Bash",
        result: "ok",
        durationMs: 3500,
        timestamp: "2026-05-01T12:00:00.000Z",
      },
    });
    expect(wrapper.text()).toMatch(/3\.5s|3500ms|3s/);
  });
});
