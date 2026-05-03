import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import EngineNudge from "@/components/stage/EngineNudge.vue";

describe("EngineNudge", () => {
  beforeEach(() => { setActivePinia(createPinia()); });

  it("renders nudge text", () => {
    const wrapper = mount(EngineNudge, {
      props: { text: "budget at 80%", timestamp: "2026-05-01T12:00:00.000Z" },
    });
    expect(wrapper.text()).toContain("budget at 80%");
  });
});
