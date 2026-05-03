import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import StreamFilters from "@/components/stage/StreamFilters.vue";

describe("StreamFilters", () => {
  beforeEach(() => { setActivePinia(createPinia()); });

  it("renders filter toggles", () => {
    const wrapper = mount(StreamFilters, {
      props: {
        modelValue: { hideToolCalls: false, hideEngineNudges: false, proseOnly: false },
      },
    });
    const text = wrapper.text().toLowerCase();
    expect(text).toMatch(/tool|nudge|prose/);
  });

  it("emits update:modelValue when a toggle is clicked", async () => {
    const wrapper = mount(StreamFilters, {
      props: {
        modelValue: { hideToolCalls: false, hideEngineNudges: false, proseOnly: false },
      },
    });
    const checkboxes = wrapper.findAll("input[type='checkbox']");
    if (checkboxes.length > 0) {
      await checkboxes[0]!.trigger("change");
      expect(wrapper.emitted("update:modelValue")).toBeTruthy();
    }
  });
});
