import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import SubAgentRegion from "@/components/stage/SubAgentRegion.vue";

describe("SubAgentRegion", () => {
  beforeEach(() => { setActivePinia(createPinia()); });

  it("renders subagent id and starts collapsed", () => {
    const wrapper = mount(SubAgentRegion, {
      props: {
        subagentId: "subagent-0",
        messageCount: 12,
        toolCallCount: 5,
        costUsd: 0.42,
        state: "SUCCEEDED",
      },
    });
    expect(wrapper.text()).toContain("subagent-0");
    // collapsed by default — expanded content not present
    expect(wrapper.find("[data-expanded]").exists()).toBe(false);
  });

  it("expands on click", async () => {
    const wrapper = mount(SubAgentRegion, {
      props: {
        subagentId: "subagent-1",
        messageCount: 3,
        toolCallCount: 1,
        costUsd: 0.10,
        state: "RUNNING",
      },
    });
    const toggle = wrapper.find("[data-toggle]");
    await toggle.trigger("click");
    expect(wrapper.find("[data-expanded]").exists()).toBe(true);
  });

  it("renders message and tool call counts", () => {
    const wrapper = mount(SubAgentRegion, {
      props: {
        subagentId: "subagent-2",
        messageCount: 7,
        toolCallCount: 3,
        costUsd: 0.25,
        state: "RUNNING",
      },
    });
    expect(wrapper.text()).toContain("7");
    expect(wrapper.text()).toContain("3");
  });
});
