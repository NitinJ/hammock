import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import AskHumanDisplay from "@/components/hil/AskHumanDisplay.vue";

describe("AskHumanDisplay", () => {
  it("renders the question prose verbatim", () => {
    const wrapper = mount(AskHumanDisplay, {
      props: {
        question: "Should I keep going?",
        onSubmit: vi.fn(),
      },
    });
    expect(wrapper.text()).toContain("Should I keep going?");
  });

  it("disables submit until the answer is non-empty", async () => {
    const wrapper = mount(AskHumanDisplay, {
      props: {
        question: "?",
        onSubmit: vi.fn(),
      },
    });
    const btn = wrapper.find("button[type='submit']");
    expect(btn.attributes("disabled")).toBeDefined();
    await wrapper.find("textarea").setValue("yes");
    expect(btn.attributes("disabled")).toBeUndefined();
  });

  it("invokes onSubmit with the answer string", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const wrapper = mount(AskHumanDisplay, {
      props: {
        question: "?",
        onSubmit,
      },
    });
    await wrapper.find("textarea").setValue("ship it");
    await wrapper.find("form").trigger("submit");
    expect(onSubmit).toHaveBeenCalledWith("ship it");
  });
});
