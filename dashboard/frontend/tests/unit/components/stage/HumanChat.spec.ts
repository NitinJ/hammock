import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import HumanChat from "@/components/stage/HumanChat.vue";

describe("HumanChat", () => {
  beforeEach(() => { setActivePinia(createPinia()); });

  it("renders message text", () => {
    const wrapper = mount(HumanChat, {
      props: { text: "use argon2id please", timestamp: "2026-05-01T12:00:00.000Z" },
    });
    expect(wrapper.text()).toContain("use argon2id please");
  });
});
