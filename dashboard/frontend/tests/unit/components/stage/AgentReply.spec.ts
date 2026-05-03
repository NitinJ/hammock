import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import AgentReply from "@/components/stage/AgentReply.vue";

describe("AgentReply", () => {
  beforeEach(() => { setActivePinia(createPinia()); });

  it("renders reply text", () => {
    const wrapper = mount(AgentReply, {
      props: { text: "Understood, switching to argon2id.", timestamp: "2026-05-01T12:00:00.000Z" },
    });
    expect(wrapper.text()).toContain("Understood, switching to argon2id.");
  });
});
