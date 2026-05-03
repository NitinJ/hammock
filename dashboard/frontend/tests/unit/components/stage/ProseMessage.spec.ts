import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import ProseMessage from "@/components/stage/ProseMessage.vue";

describe("ProseMessage", () => {
  beforeEach(() => { setActivePinia(createPinia()); });

  it("renders the message text", () => {
    const wrapper = mount(ProseMessage, {
      props: {
        text: "I will now implement the feature.",
        timestamp: "2026-05-01T12:00:00.000Z",
      },
    });
    expect(wrapper.text()).toContain("I will now implement the feature.");
  });

  it("renders a timestamp", () => {
    const wrapper = mount(ProseMessage, {
      props: { text: "hello", timestamp: "2026-05-01T12:00:00.000Z" },
    });
    // some time representation present
    expect(wrapper.html()).toMatch(/12:00|2026/);
  });
});
